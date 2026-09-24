# Adult League schedule & scores

Reads the league's Google Sheet and shows the schedule, posted scores, and
standings. It can be published two ways from the same parsing code:

- **Static site on GitHub Pages** (`build.py`): free, works with a custom domain,
  never sleeps, and loads instantly. A GitHub Action rebuilds it from the sheet
  every 15 minutes.
- **Streamlit app** (`app.py`): the original version. Reads the sheet live.

The sheet must be shared **"Anyone with the link can view."**

## Static site

### One-time setup

1. Merge this into `main`.
2. On GitHub, go to **Settings → Pages → Build and deployment → Source** and pick
   **GitHub Actions**.
3. Open the **Actions** tab, choose **Publish site**, and click **Run workflow**
   (or wait for the next scheduled run). The site appears at
   `https://mhendri.github.io/basketball/`.

**Custom domain:** under **Settings → Pages → Custom domain**, enter the domain.
Then add the DNS record GitHub shows you (a `CNAME` pointing at
`mhendri.github.io` for a subdomain like `league.example.com`).

### How it works

`build.py` fetches the sheet, parses it with `league.py`, and writes one
self-contained page, `_site/index.html`, with the data embedded. The page's
JavaScript (`web/app.js`) does everything that depends on the current time, in
the browser: "Tonight", what's up next, and "Score not posted". Those stay
correct between rebuilds. `web/app.js` renders the same HTML as the render
functions in `app.py`, so the two versions look the same.

- **Update delay.** Scores show up within about 15 minutes of being entered in
  the sheet. GitHub sometimes starts scheduled runs a few minutes late.
- **Bad fetches don't publish.** If the sheet can't be read, the build fails and
  the last good page stays up. If the page ever goes more than 6 hours without a
  rebuild, it shows a notice saying so.
- **Off-season pause.** GitHub turns off scheduled workflows in a repo with no
  activity for 60 days. To turn it back on, go to **Actions → Publish site →
  Enable workflow**.

### New season or new sheet

No code change needed. Under **Settings → Secrets and variables → Actions →
Variables**, set `LEAGUE_SHEET_URL` to the new sheet's link. Set `LEAGUE_TZ` too
if the league isn't in `America/New_York`. Then run the workflow.

### Build and preview locally

```bash
pip install requests tzdata
python build.py                      # live sheet -> _site/index.html
python build.py --source league.csv  # or a local CSV export of the sheet
```

Open `_site/index.html` in a browser. Two URL parameters help with checking:

- `?team=Kaplan` opens the page filtered to one team. Picking a team updates
  the URL, so a filtered link can be shared.
- `?now=2026-10-01T20:45` shows the page as it would look at that moment.

## Streamlit app

```bash
pip install -r requirements.txt
streamlit run app.py
```

Environment overrides: `LEAGUE_SHEET_URL` (sheet link or local CSV path),
`LEAGUE_TZ`, and `LEAGUE_NOW` (ISO datetime, to preview the app as of a given
moment).

## Files

| Path | What it is |
| --- | --- |
| `league.py` | Loading and parsing the sheet, and computing standings. Shared by both versions |
| `build.py` | Builds the static site |
| `web/` | The static page: HTML template, styles, and browser-side rendering |
| `.github/workflows/pages.yml` | Rebuilds and publishes the site on a schedule |
| `app.py`, `.streamlit/`, `requirements.txt` | The Streamlit app |

The parser looks for a row of dates (like `9/8`) with game times (like `8:30`)
in the first column, division tables with W–L records, and a `Scores` block
below. Standings are computed from posted scores, except that the sheet's own
W–L column is used when it isn't behind the scores.
