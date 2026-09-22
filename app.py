"""
Adult League schedule & scores — reads the league's Google Sheet live.

Run:
    pip install -r requirements.txt
    streamlit run app.py

The sheet must be shared "Anyone with the link can view" (it is).
Env overrides: LEAGUE_SHEET_URL (sheet link or local CSV path), LEAGUE_TZ,
LEAGUE_NOW (ISO datetime, to preview the app as of a given moment).
"""
from __future__ import annotations

import csv
import difflib
import html
import io
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import requests
import streamlit as st

SHEET_URL = os.environ.get(
    "LEAGUE_SHEET_URL",
    "https://docs.google.com/spreadsheets/d/1IBgQlW1lqmNIjhSQ8v4K33X-zyU2-1aoKHrIYrmLqWY/edit?usp=sharing",
)
TZ = ZoneInfo(os.environ.get("LEAGUE_TZ", "America/New_York"))
GAME_LENGTH = timedelta(minutes=60)
REFRESH_SECONDS = 300
ALL = "All teams"

DATE_RE = re.compile(r"^(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?$")
TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})\s*([ap])?\.?m?\.?$", re.I)
SEP_RE = re.compile(r"\s+(?:vs?\.?|versus|@)\s+", re.I)
NO_GAME_RE = re.compile(r"\bno\s+games?\b", re.I)
TBD_RE = re.compile(r"\b(tbd|tba|playoffs?|semis?|finals?|championship)\b", re.I)
SCORE_RE = re.compile(r"^(.*?\S)\s*[-:]?\s+(\d{1,3})$")
RECORD_RE = re.compile(r"^(\d+)\s*[-–]\s*(\d+)$")
DIV_RE = re.compile(r"^(division|div\.?)\s+\S+$", re.I)


# ---------------------------------------------------------------- data model
@dataclass
class Result:
    date: date
    t1: str
    p1: int
    t2: str
    p2: int
    note: str = ""
    time: time | None = None

    @property
    def winner(self): return self.t1 if self.p1 >= self.p2 else self.t2
    @property
    def loser(self): return self.t2 if self.p1 >= self.p2 else self.t1
    @property
    def hi(self): return max(self.p1, self.p2)
    @property
    def lo(self): return min(self.p1, self.p2)
    def has(self, team): return team in (self.t1, self.t2)


@dataclass
class Game:
    date: date
    time: time | None
    kind: str  # "game" | "tbd" | "off"
    label: str
    t1: str | None = None
    t2: str | None = None
    stage: str = ""
    result: Result | None = None

    @property
    def start(self):
        return datetime.combine(self.date, self.time or time(20, 0))
    def has(self, team): return team in (self.t1, self.t2)
    def opponent(self, team): return self.t2 if self.t1 == team else self.t1


@dataclass
class League:
    title: str
    season: str
    divisions: dict
    records: dict
    games: list
    results: list
    notes: dict

    @property
    def teams(self): return sorted({t for ts in self.divisions.values() for t in ts})


# ------------------------------------------------------------------- loading
class SheetError(Exception):
    pass


def csv_url(url: str) -> str:
    if "format=csv" in url or "output=csv" in url:
        return url
    m = re.search(r"/d/([\w-]+)", url)
    if not m:
        raise SheetError("LEAGUE_SHEET_URL isn't a Google Sheets link.")
    gid = re.search(r"gid=(\d+)", url)
    return f"https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=csv" + (
        f"&gid={gid.group(1)}" if gid else "")


@st.cache_data(ttl=REFRESH_SECONDS, show_spinner=False)
def fetch_rows(source: str) -> tuple[list[list[str]], datetime]:
    if os.path.exists(source):
        with open(source, newline="", encoding="utf-8-sig") as f:
            text = f.read()
    else:
        resp = requests.get(csv_url(source), timeout=15)
        resp.raise_for_status()
        if "html" in resp.headers.get("content-type", ""):
            raise SheetError("The sheet isn't public. Set sharing to “Anyone with the link can view”.")
        text = resp.content.decode("utf-8-sig")
    rows = [[" ".join(c.replace("\xa0", " ").split()) for c in r] for r in csv.reader(io.StringIO(text))]
    return rows, datetime.now(TZ)


@st.cache_resource
def last_good() -> dict:
    return {}


# ------------------------------------------------------------------- parsing
def cell(rows, r, c):
    return rows[r][c] if r < len(rows) and c < len(rows[r]) else ""


def resolve_team(name: str, teams: list[str]) -> str | None:
    n = name.strip().lower()
    if not n:
        return None
    lower = {t.lower(): t for t in teams}
    if n in lower:
        return lower[n]
    pref = [t for t in teams if t.lower().startswith(n)] if len(n) >= 3 else []
    if len(pref) == 1:
        return pref[0]
    close = difflib.get_close_matches(n, list(lower), n=1, cutoff=0.8)
    return lower[close[0]] if close else None


def teams_in_text(text, teams):
    hits = [(m.start(), t) for t in teams
            for m in [re.search(rf"\b{re.escape(t)}\b", text, re.I)] if m]
    return [t for _, t in sorted(hits)]


def parse_league(rows: list[list[str]], today: date) -> League:
    # Title / season
    title = next((c for r in rows[:10] for c in r if re.search(r"[A-Za-z]{3}", c)), "League schedule")
    m = re.search(r"\b(Fall|Autumn|Spring|Summer|Winter)\s+(\d{4})", title, re.I)
    season = m.group(0) if m else ""
    name = re.sub(r"\bschedules?\b", "", title.replace(season, ""), flags=re.I).strip(" -–:") or title
    year_m = re.search(r"\b(20\d{2})\b", title)

    # Divisions + official W-L
    divisions, records = {}, {}
    for r, row in enumerate(rows):
        for c, v in enumerate(row):
            if DIV_RE.match(v):
                teams, rr = [], r + 1
                while cell(rows, rr, c):
                    t = cell(rows, rr, c)
                    teams.append(t)
                    rec = RECORD_RE.match(cell(rows, rr, c + 1))
                    if rec:
                        records[t] = (int(rec.group(1)), int(rec.group(2)))
                    rr += 1
                divisions[v] = teams
    known = [t for ts in divisions.values() for t in ts]

    # Date header rows (2+ date cells, above the Scores block)
    score_anchor = next((r for r, row in enumerate(rows)
                         if any(re.fullmatch(r"(scores?|results?)", c, re.I) for c in row)), len(rows))
    headers = []
    for r in range(score_anchor):
        cols = {c: DATE_RE.match(v) for c, v in enumerate(rows[r]) if DATE_RE.match(v)}
        if len(cols) >= 2:
            headers.append((r, cols))
    start_month = int(next(iter(headers[0][1].values())).group(1)) if headers else today.month
    base_year = int(year_m.group(1)) if year_m else today.year

    def to_date(dm) -> date:
        mo, dy, yr = int(dm.group(1)), int(dm.group(2)), dm.group(3)
        if yr:
            y = int(yr) + (2000 if len(yr) == 2 else 0)
        else:
            y = base_year + (1 if mo < start_month else 0)
        return date(y, mo, dy)

    def gameish(v):
        return bool(v) and bool(NO_GAME_RE.search(v) or SEP_RE.search(v) or TBD_RE.search(v)
                                or len(teams_in_text(v, known)) >= 2)

    games, notes, slot_times = [], defaultdict(list), []
    for r, cols in headers:
        stage = ""
        block_rows, rr = [], r + 1
        while any(gameish(cell(rows, rr, c)) for c in cols):
            block_rows.append(rr)
            rr += 1
        if any(re.search("playoff", cell(rows, x, c), re.I) for x in block_rows for c in cols):
            stage = "Playoffs"
        for slot, x in enumerate(block_rows):
            label = next((v for c, v in enumerate(rows[x]) if c < min(cols) and TIME_RE.match(v)), None)
            if label:
                tm = TIME_RE.match(label)
                h, mi, ap = int(tm.group(1)), int(tm.group(2)), (tm.group(3) or "p").lower()
                t = time((h % 12) + (12 if ap == "p" else 0), mi)
                if len(slot_times) <= slot:
                    slot_times.append(t)
            else:
                t = slot_times[slot] if slot < len(slot_times) else None
            for c, dm in cols.items():
                v = cell(rows, x, c)
                if not v:
                    continue
                d = to_date(dm)
                if NO_GAME_RE.search(v):
                    games.append(Game(d, t, "off", v, stage=stage))
                    continue
                parts = SEP_RE.split(v, maxsplit=1)
                pair = [resolve_team(p, known) for p in parts] if len(parts) == 2 else teams_in_text(v, known)
                if len(pair) == 2 and all(pair) and pair[0] != pair[1]:
                    games.append(Game(d, t, "game", v, pair[0], pair[1], stage))
                elif gameish(v):
                    games.append(Game(d, t, "tbd", SEP_RE.sub(" vs ", v), stage=stage))
                else:
                    notes[d].append(v)
        # footnote rows directly under the grid
        while any(cell(rows, rr, c) for c in cols):
            texts = {c: cell(rows, rr, c).strip("* ").strip() for c in cols if cell(rows, rr, c)}
            first = next((t for t in texts.values() if t), "")
            for c, t in texts.items():
                note = t or first
                if note and note not in notes[to_date(cols[c])]:
                    notes[to_date(cols[c])].append(note)
            rr += 1

    # Scores
    results, cur = [], None
    for row in rows[score_anchor + 1:]:
        dcell = next((DATE_RE.match(v) for v in row if DATE_RE.match(v)), None)
        if dcell:
            cur = to_date(dcell)
        pts = [(i, SCORE_RE.match(v)) for i, v in enumerate(row) if SCORE_RE.match(v) and not DATE_RE.match(v)]
        if cur and len(pts) >= 2:
            (i1, a), (i2, b) = pts[:2]
            extra = " ".join(v for i, v in enumerate(row) if v and i not in (i1, i2) and not DATE_RE.match(v))
            t1 = resolve_team(a.group(1), known) or a.group(1)
            t2 = resolve_team(b.group(1), known) or b.group(1)
            res = Result(cur, t1, int(a.group(2)), t2, int(b.group(2)), extra)
            for g in games:
                if g.kind == "game" and g.date == cur and {g.t1, g.t2} == {t1, t2} and not g.result:
                    g.result, res.time = res, g.time
                    break
            results.append(res)

    if not divisions:  # fallback: one table of every team seen
        seen = sorted({t for g in games if g.kind == "game" for t in (g.t1, g.t2)}
                      | {t for r in results for t in (r.t1, r.t2)})
        divisions = {"Standings": seen}

    games.sort(key=lambda g: (g.date, g.time or time(0)))
    return League(name, season, divisions, records, games, results, dict(notes))


# ----------------------------------------------------------------- standings
def standings(lg: League):
    stats = {t: dict(w=0, l=0, pf=0, pa=0, seq=[]) for t in lg.teams}
    for r in sorted(lg.results, key=lambda r: r.date):
        for team, us, them in ((r.t1, r.p1, r.p2), (r.t2, r.p2, r.p1)):
            if team in stats:
                s = stats[team]
                s["pf"] += us
                s["pa"] += them
                s["seq"].append("W" if us > them else "L")
                s["w" if us > them else "l"] += 1
    out = {}
    for div, teams in lg.divisions.items():
        rows = []
        for t in teams:
            s = stats[t]
            w, l = s["w"], s["l"]
            if t in lg.records and sum(lg.records[t]) >= w + l:  # the sheet's W-L wins unless it's behind
                w, l = lg.records[t]
            streak = ""
            if s["seq"]:
                last = s["seq"][-1]
                n = len(s["seq"]) - len("".join(s["seq"]).rstrip(last))
                streak = f"{last}{n}"
            rows.append(dict(team=t, w=w, l=l, pf=s["pf"], pa=s["pa"], diff=s["pf"] - s["pa"], streak=streak))
        rows.sort(key=lambda x: (-(x["w"] / (x["w"] + x["l"]) if x["w"] + x["l"] else 0), -x["diff"], x["team"]))
        if rows:
            lw, ll = rows[0]["w"], rows[0]["l"]
            for x in rows:
                x["gb"] = ((lw - x["w"]) + (x["l"] - ll)) / 2
        out[div] = rows
    return out


# ----------------------------------------------------------------- rendering
e = html.escape


def fmt_time(t):
    return t.strftime("%-I:%M %p") if t else "TBD"


def fmt_day(d):
    return d.strftime("%a, %b %-d")


def relative(d, today):
    delta = (d - today).days
    return {0: "Tonight", 1: "Tomorrow"}.get(delta, f"In {delta} days" if 1 < delta < 7 else "")


def ordinal(n):
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def show(html_str):
    st.markdown(html_str.replace("\n", ""), unsafe_allow_html=True)


CSS = """
<style>
.stApp{font-family:'Archivo','Helvetica Neue',Arial,sans-serif}
[data-testid="stHeader"]{background:transparent}
.block-container,[data-testid="stMainBlockContainer"]{padding-top:2.2rem;max-width:720px}
.lg-title{font-weight:850;font-stretch:68%;font-size:clamp(2.6rem,11vw,3.8rem);line-height:.9;letter-spacing:-.01em;color:#1D2B53;margin:0}
.lg-sub{color:#5D6782;font-size:1rem;margin:.35rem 0 1rem}
.lg-hero{position:relative;overflow:hidden;border-radius:14px;padding:1.2rem 1.25rem 1.3rem 1.5rem;margin:.4rem 0 1rem;color:#1D2B53;
 border-left:7px solid #E8631A;
 background:radial-gradient(circle at 104% 118%,transparent 0 150px,rgba(29,43,83,.28) 150px 153px,transparent 153px),
 repeating-linear-gradient(90deg,transparent 0 55px,rgba(110,62,18,.13) 55px 56px),
 repeating-linear-gradient(90deg,rgba(255,255,255,.10) 0 56px,rgba(120,70,20,.05) 56px 112px,rgba(255,255,255,.04) 112px 168px),#E7C593}
.lg-kicker{font-size:.95rem;font-weight:600;color:#7A3A0A}
.lg-big{font-weight:850;font-stretch:68%;font-size:clamp(2.1rem,9vw,3rem);line-height:1;margin:.1rem 0 .8rem}
.lg-hrow{display:grid;grid-template-columns:5.2rem 1fr;align-items:baseline;gap:.5rem;padding:.2rem 0}
.lg-htime{font-variant-numeric:tabular-nums;font-weight:600}
.lg-hmatch{font-weight:800;font-stretch:78%;font-size:1.45rem;line-height:1.15}
.lg-hmatch .vs,.lg-match .vs{font-weight:400;font-stretch:100%;font-size:.8em;opacity:.65;padding:0 .2em}
.lg-hline{margin:.15rem 0;font-size:1.02rem}
.lg-day{display:flex;justify-content:space-between;align-items:baseline;margin:1.3rem 0 .25rem;padding-bottom:.3rem;border-bottom:2px solid #1D2B53}
.lg-dayname{font-weight:800;font-stretch:78%;font-size:1.25rem;color:#1D2B53}
.lg-rel{color:#B4490C;font-weight:600;font-size:.9rem}
.lg-note{color:#5D6782;font-size:.88rem;margin:.2rem 0}
.lg-row{display:grid;grid-template-columns:5.2rem 1fr auto;gap:.5rem;align-items:center;padding:.55rem 0;border-bottom:1px solid #D5DBE5;color:#1D2B53}
.lg-time{font-variant-numeric:tabular-nums;color:#5D6782;font-size:.95rem}
.lg-match{font-weight:600;font-size:1.05rem}
.lg-tag{font-size:.78rem;font-weight:600;border-radius:4px;padding:.1rem .4rem;background:#E6EAF1;color:#5D6782;white-space:nowrap}
.lg-tag.w{background:#1D2B53;color:#fff}.lg-tag.ot{background:#FBE3D3;color:#8A3608}
.lg-muted{color:#5D6782;font-weight:400}
.lg-score{display:grid;grid-template-columns:1fr 3rem;row-gap:.05rem}
.lg-score .t{font-size:1.05rem;color:#8A93A7}.lg-score .p{text-align:right;font-weight:800;font-stretch:72%;font-size:1.35rem;font-variant-numeric:tabular-nums;color:#9AA2B4;line-height:1.15}
.lg-score .won{color:#1D2B53;font-weight:700}.lg-score .p.won{color:#1D2B53}
.lg-div{font-weight:800;font-stretch:78%;font-size:1.3rem;margin:1.2rem 0 .3rem;color:#1D2B53}
.lg-wrap{overflow-x:auto}
table.lg-tbl{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums;color:#1D2B53;border:none!important}
table.lg-tbl th,table.lg-tbl td{border:none!important;border-bottom:1px solid #D5DBE5!important;padding:.5rem .4rem!important;text-align:right;background:transparent!important}
table.lg-tbl th:first-child,table.lg-tbl td:first-child{text-align:left}
table.lg-tbl thead th{color:#5D6782;font-weight:600;font-size:.85rem;border-bottom:2px solid #1D2B53!important}
table.lg-tbl tbody th{font-weight:700}
table.lg-tbl tr.mine{background:#FBE3D3!important}
table.lg-tbl tr.mine th,table.lg-tbl tr.mine td{background:#FBE3D3!important}
@media (max-width:440px){.lg-pf{display:none}}
.lg-empty{color:#5D6782;padding:1rem 0}
</style>
"""


def matchup_html(g: Game, team: str | None):
    if g.kind == "off":
        return '<span class="lg-muted">No games</span>'
    if g.kind == "tbd":
        lbl = "Playoff game" if re.search("playoff", g.label, re.I) else g.label
        return f'{e(lbl)} <span class="lg-muted">(teams TBD)</span>'
    if team:
        return f'<span class="vs">vs</span> {e(g.opponent(team))}'
    return f'{e(g.t1)} <span class="vs">vs</span> {e(g.t2)}'


def score_html(r: Result, team: str | None):
    rows = ""
    for t, p in sorted(((r.t1, r.p1), (r.t2, r.p2)), key=lambda x: -x[1]):
        cls = " won" if t == r.winner else ""
        rows += f'<div class="t{cls}">{e(t)}</div><div class="p{cls}">{p}</div>'
    tags = ""
    if team:
        won = r.winner == team
        tags += f'<span class="lg-tag {"w" if won else ""}">{"W" if won else "L"}</span> '
    if r.note:
        tags += f'<span class="lg-tag ot">{e(r.note)}</span>'
    return f'<div class="lg-score">{rows}</div>', tags


def day_header(d, today, notes):
    rel = relative(d, today)
    out = f'<div class="lg-day"><span class="lg-dayname">{e(fmt_day(d))}</span><span class="lg-rel">{e(rel)}</span></div>'
    for n in notes.get(d, []):
        out += f'<div class="lg-note">Note: {e(n)}</div>'
    return out


def render_hero(lg: League, team, now, table):
    today = now.date()
    upcoming = [g for g in lg.games if g.kind != "off" and g.start + GAME_LENGTH > now and not g.result]
    if team:
        div, rank, row = next(((d, i + 1, x) for d, rows in table.items()
                               for i, x in enumerate(rows) if x["team"] == team), (None, None, None))
        nxt = next((g for g in upcoming if g.has(team) or g.kind == "tbd"), None)
        last = next((r for r in sorted(lg.results, key=lambda r: r.date, reverse=True) if r.has(team)), None)
        lines = []
        if row:
            lines.append(f'{row["w"]}–{row["l"]}, {ordinal(rank)} in {e(div)}')
        if nxt:
            vs = f"vs {e(nxt.opponent(team))}" if nxt.kind == "game" else "matchup TBD"
            lines.append(f'<b>Next:</b> {e(fmt_day(nxt.date))}, {e(fmt_time(nxt.time))} {vs}')
        else:
            lines.append("No more games scheduled.")
        if last:
            verb = "Beat" if last.winner == team else "Lost to"
            opp = last.t2 if last.t1 == team else last.t1
            ot = " in OT" if "ot" in last.note.lower() else ""
            lines.append(f'<b>Last:</b> {verb} {e(opp)} {last.hi}–{last.lo}{ot}, {e(fmt_day(last.date))}')
        body = "".join(f'<div class="lg-hline">{x}</div>' for x in lines)
        kicker = relative(nxt.date, today) if nxt and nxt.has(team) and relative(nxt.date, today) in ("Tonight", "Tomorrow") else "Your team"
        show(f'<div class="lg-hero"><div class="lg-kicker">{e(kicker)}</div><div class="lg-big">{e(team)}</div>{body}</div>')
        return
    if not upcoming:
        show('<div class="lg-hero"><div class="lg-kicker">Season complete</div>'
             '<div class="lg-big">That’s a wrap</div><div class="lg-hline">Final scores and standings are below.</div></div>')
        return
    d = upcoming[0].date
    night = [g for g in lg.games if g.date == d and g.kind != "off"]
    rows = "".join(f'<div class="lg-hrow"><span class="lg-htime">{e(fmt_time(g.time))}</span>'
                   f'<span class="lg-hmatch">{matchup_html(g, None)}</span></div>' for g in night)
    kicker = relative(d, today) or "Up next"
    show(f'<div class="lg-hero"><div class="lg-kicker">{e(kicker)}</div><div class="lg-big">{e(fmt_day(d))}</div>{rows}</div>')


def render_schedule(lg: League, team, now, show_past):
    today = now.date()
    by_day = defaultdict(list)
    for g in lg.games:
        if not show_past and g.date < today:
            continue
        by_day[g.date].append(g)
    out = ""
    for d, games in sorted(by_day.items()):
        if team:
            mine = [g for g in games if g.has(team)]
            tbd = [g for g in games if g.kind == "tbd"]
            if mine:
                games = mine
            elif tbd:
                times = " & ".join(fmt_time(g.time).replace(" PM", "") for g in tbd) + " PM"
                lbl = "Playoff games" if tbd[0].stage else "League games"
                out += day_header(d, today, lg.notes) + (
                    f'<div class="lg-row"><span class="lg-time">{e(times)}</span>'
                    f'<span class="lg-match">{lbl} <span class="lg-muted">(matchups TBD)</span></span><span></span></div>')
                continue
            elif all(g.kind == "off" for g in games):
                games = games[:1]
            else:
                continue  # team is off this night
        elif all(g.kind == "off" for g in games):
            games = games[:1]
        out += day_header(d, today, lg.notes)
        for g in games:
            t = "" if g.kind == "off" else e(fmt_time(g.time))
            if g.result:
                sc, tags = score_html(g.result, team)
                out += f'<div class="lg-row"><span class="lg-time">{t}</span>{sc}<span>{tags}</span></div>'
            else:
                right = '<span class="lg-tag">Score not posted</span>' if g.kind == "game" and g.start + GAME_LENGTH < now else ""
                stage = f'<span class="lg-tag">{e(g.stage)}</span>' if g.stage and g.kind != "off" and not right else ""
                out += (f'<div class="lg-row"><span class="lg-time">{t}</span>'
                        f'<span class="lg-match">{matchup_html(g, team)}</span><span>{right or stage}</span></div>')
    show(out or '<div class="lg-empty">No games left on the schedule. Turn on “Show played games” to see the season.</div>')


def render_scores(lg: League, team, today):
    res = [r for r in lg.results if not team or r.has(team)]
    if not res:
        show('<div class="lg-empty">No scores posted yet. Results show up here after each game night.</div>')
        return
    by_day = defaultdict(list)
    for r in res:
        by_day[r.date].append(r)
    out = ""
    for d in sorted(by_day, reverse=True):
        out += day_header(d, today, {})
        for r in sorted(by_day[d], key=lambda r: r.time or time(0)):
            sc, tags = score_html(r, team)
            out += f'<div class="lg-row"><span class="lg-time">{e(fmt_time(r.time)) if r.time else ""}</span>{sc}<span>{tags}</span></div>'
    show(out)


def render_standings(table, team):
    out = ""
    for div, rows in table.items():
        out += (f'<div class="lg-div">{e(div)}</div><div class="lg-wrap"><table class="lg-tbl"><thead><tr>'
                '<th scope="col">Team</th><th scope="col">W</th><th scope="col">L</th><th scope="col">GB</th>'
                '<th scope="col" class="lg-pf">PF</th><th scope="col" class="lg-pf">PA</th>'
                '<th scope="col">+/−</th><th scope="col">Streak</th></tr></thead><tbody>')
        for x in rows:
            gb = "—" if x["gb"] == 0 else (f'{x["gb"]:.0f}' if x["gb"] == int(x["gb"]) else f'{x["gb"]:.1f}')
            diff = f'+{x["diff"]}' if x["diff"] > 0 else (f'−{-x["diff"]}' if x["diff"] < 0 else "0")
            cls = ' class="mine"' if x["team"] == team else ""
            out += (f'<tr{cls}><th scope="row">{e(x["team"])}</th><td>{x["w"]}</td><td>{x["l"]}</td><td>{gb}</td>'
                    f'<td class="lg-pf">{x["pf"]}</td><td class="lg-pf">{x["pa"]}</td><td>{diff}</td>'
                    f'<td>{e(x["streak"]) or "—"}</td></tr>')
        out += "</tbody></table></div>"
    show(out)
    st.caption("GB is games behind the division leader. PF, PA and +/− come from posted scores.")


# ----------------------------------------------------------------------- app
def main():
    st.set_page_config(page_title="League schedule & scores", page_icon="🏀", layout="centered")
    show(CSS)

    now = datetime.fromisoformat(os.environ["LEAGUE_NOW"]) if os.environ.get("LEAGUE_NOW") \
        else datetime.now(TZ).replace(tzinfo=None)

    cache = last_good()
    try:
        rows, fetched = fetch_rows(SHEET_URL)
        cache.update(rows=rows, fetched=fetched)
    except Exception as ex:  # network hiccup or sharing change: fall back to the last good copy
        if "rows" not in cache:
            st.error(f"Couldn't load the league sheet. {ex}")
            st.stop()
        rows, fetched = cache["rows"], cache["fetched"]
        st.warning(f"Couldn't reach the league sheet, so this is the copy from {fetched:%-I:%M %p}.")

    lg = parse_league(rows, now.date())
    if not lg.games and not lg.results:
        st.error("Couldn't find a schedule in the sheet. The app looks for a row of dates (like 9/8) "
                 "with game times (like 8:30) in the first column.")
        st.stop()
    table = standings(lg)

    show(f'<div role="heading" aria-level="1" class="lg-title">{e(lg.title)}</div>'
         f'<div class="lg-sub">{e(lg.season + " " if lg.season else "")}schedule and scores</div>')

    options = [ALL] + lg.teams
    if st.session_state.get("team") not in options:
        st.session_state.team = resolve_team(st.query_params.get("team", ""), lg.teams) or ALL
    choice = st.selectbox("Team", options, key="team")
    team = None if choice == ALL else choice
    if team:
        st.query_params["team"] = team
    elif "team" in st.query_params:
        del st.query_params["team"]

    render_hero(lg, team, now, table)

    tab_s, tab_r, tab_t = st.tabs(["Schedule", "Scores", "Standings"])
    with tab_s:
        past = st.toggle("Show played games", value=False)
        render_schedule(lg, team, now, past)
    with tab_r:
        render_scores(lg, team, now.date())
    with tab_t:
        render_standings(table, team)

    st.divider()
    link = SHEET_URL if SHEET_URL.startswith("http") else None
    src = f"[league sheet]({link})" if link else "league sheet"
    st.caption(f"Pulled from the {src} every {REFRESH_SECONDS // 60} minutes. Last checked {fetched:%-I:%M %p}.")
    if st.button("Refresh now"):
        fetch_rows.clear()
        st.rerun()


if __name__ == "__main__":
    main()
