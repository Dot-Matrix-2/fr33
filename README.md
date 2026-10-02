# Free in NYC

An information-only website listing every strictly free event in New York City for the next month.

## Folder layout

| Path | What it is |
|---|---|
| `website/public/` | **The website.** This is the only folder that gets published. `index.html` + `events.js`. |
| `website/update_site.py` | Turns the collector's output into `website/public/events.js`. |
| `website/build_db.py` | Shared cleanup rules (category, borough, canceled-event filter) used by `update_site.py`. |
| `fr33_collector/` | The data collector (`collect.py`). Writes `out/events.json` and `out/events.csv`. |
| `.github/workflows/refresh-events.yml` | Runs the collector twice a day on GitHub and commits fresh data. |
| `_archive/` | Old drafts, not used, not uploaded to GitHub. |

## Refresh locally

```
pip install requests icalendar python-dateutil
cd fr33_collector && python collect.py --days 31 && cd ..
python website/update_site.py
```
Then open `website/public/index.html` in a browser.

## Hosting

GitHub repo -> Cloudflare Pages (build output directory: `website/public`, no build command).
The GitHub Action refreshes the data at about 6am and 6pm New York time; each commit triggers a Cloudflare redeploy.
