# Adult League schedule & scores

A [Streamlit](https://streamlit.io) app that reads the league's Google Sheet
live and renders the schedule, posted scores, and computed standings.

## Run it

```bash
pip install -r requirements.txt
streamlit run app.py
```

The sheet must be shared **"Anyone with the link can view."**

## Configuration

Everything is driven by environment variables — no code changes needed:

| Variable | Default | Purpose |
| --- | --- | --- |
| `LEAGUE_SHEET_URL` | the league's sheet | Google Sheets link, or a path to a local CSV |
| `LEAGUE_TZ` | `America/New_York` | Timezone used for game times |
| `LEAGUE_NOW` | current time | ISO datetime, to preview the app as of a given moment |

## How it works

- The sheet is fetched as CSV and cached for 5 minutes; a manual **Refresh now**
  button clears the cache. If the fetch fails, the last good copy is served with
  a warning rather than erroring out.
- The parser looks for a row of dates (like `9/8`) with game times (like `8:30`)
  in the first column, plus a `Scores` block below the grid.
- Standings are computed from posted scores, except that the sheet's own W–L
  column wins when it isn't behind the scores.
- Picking a team filters every tab and is reflected in the `?team=` query param,
  so a filtered view is shareable.
