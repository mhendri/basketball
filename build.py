"""
Build the static site: fetch the league sheet, parse it, and write one
self-contained page with the data embedded. The page (web/app.js) does the
date-dependent rendering in the browser, so it stays correct between builds.

Run:
    pip install requests tzdata
    python build.py                              # live sheet -> _site/index.html
    python build.py --source league.csv --out /tmp/site

Uses the same LEAGUE_SHEET_URL / LEAGUE_TZ overrides as the Streamlit app.
Exits non-zero, without writing anything, if the sheet can't be read or has
no schedule, so a bad fetch never replaces a good published page.
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from datetime import datetime
from pathlib import Path

from league import SHEET_URL, TZ, League, load_rows, parse_league, standings

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"


def league_data(lg: League, fetched: datetime, source: str) -> dict:
    index = {id(r): i for i, r in enumerate(lg.results)}
    hm = lambda t: t.strftime("%H:%M") if t else None  # noqa: E731
    return {
        "title": lg.title,
        "season": lg.season,
        "tz": TZ.key,
        "fetched": fetched.isoformat(timespec="minutes"),
        "sheet": source if source.startswith("http") else None,
        "teams": lg.teams,
        "games": [dict(date=g.date.isoformat(), time=hm(g.time), kind=g.kind, label=g.label,
                       t1=g.t1, t2=g.t2, stage=g.stage,
                       result=index[id(g.result)] if g.result else None) for g in lg.games],
        "results": [dict(date=r.date.isoformat(), time=hm(r.time), t1=r.t1, p1=r.p1,
                         t2=r.t2, p2=r.p2, note=r.note) for r in lg.results],
        "notes": {d.isoformat(): n for d, n in sorted(lg.notes.items())},
        "standings": [[div, rows] for div, rows in standings(lg).items()],
    }


def render_page(lg: League, data: dict) -> str:
    # "<" is escaped so nothing in the sheet can close the <script> block early
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    subtitle = f"{lg.season} schedule and scores" if lg.season else "Schedule and scores"
    page = (WEB / "index.html").read_text(encoding="utf-8")
    for key, value in (
        ("{{PAGE_TITLE}}", html.escape(f"{lg.title} {subtitle.lower()}")),
        ("{{TITLE}}", html.escape(lg.title)),
        ("{{SUBTITLE}}", html.escape(subtitle)),
        ("/*STYLE*/", (WEB / "style.css").read_text(encoding="utf-8")),
        ("/*SCRIPT*/", (WEB / "app.js").read_text(encoding="utf-8")),
        ("{{DATA}}", blob),  # last, so sheet text is never scanned for placeholders
    ):
        page = page.replace(key, value)
    return page


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Build the static league site.")
    ap.add_argument("--source", default=SHEET_URL, help="Google Sheets link or local CSV path")
    ap.add_argument("--out", default=ROOT / "_site", type=Path, help="output directory")
    args = ap.parse_args(argv)

    rows = load_rows(args.source)
    fetched = datetime.now(TZ)
    lg = parse_league(rows, fetched.date())
    if not lg.games and not lg.results:
        sys.exit("Couldn't find a schedule in the sheet. It needs a row of dates (like 9/8) "
                 "with game times (like 8:30) in the first column. Nothing was published.")

    page = render_page(lg, league_data(lg, fetched, args.source))
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "index.html").write_text(page, encoding="utf-8")
    print(f"Built {args.out / 'index.html'}: {lg.title} {lg.season}, {len(lg.games)} schedule entries, "
          f"{len(lg.results)} scores, {len(lg.teams)} teams ({len(page) // 1024} KB)")


if __name__ == "__main__":
    main()
