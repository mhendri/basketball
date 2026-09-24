"""
League sheet loading, parsing and standings, shared by the Streamlit app
(app.py) and the static site build (build.py). No Streamlit imports here.
"""
from __future__ import annotations

import csv
import difflib
import io
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import requests

# `or` rather than a get() default, so an empty variable (e.g. an unset
# GitHub Actions variable) still falls back to these.
SHEET_URL = os.environ.get("LEAGUE_SHEET_URL") or (
    "https://docs.google.com/spreadsheets/d/1IBgQlW1lqmNIjhSQ8v4K33X-zyU2-1aoKHrIYrmLqWY/edit?usp=sharing")
TZ = ZoneInfo(os.environ.get("LEAGUE_TZ") or "America/New_York")

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


def load_rows(source: str) -> list[list[str]]:
    """Read the sheet (a Google Sheets link or a local CSV path) into cleaned rows."""
    if os.path.exists(source):
        with open(source, newline="", encoding="utf-8-sig") as f:
            text = f.read()
    else:
        resp = requests.get(csv_url(source), timeout=15)
        resp.raise_for_status()
        if "html" in resp.headers.get("content-type", ""):
            raise SheetError("The sheet isn't public. Set sharing to “Anyone with the link can view”.")
        text = resp.content.decode("utf-8-sig")
    return [[" ".join(c.replace("\xa0", " ").split()) for c in r] for r in csv.reader(io.StringIO(text))]


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
