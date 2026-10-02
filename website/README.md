# Free in NYC website

`public/` is the whole website: open `public/index.html` in any browser. Nothing to install.

- `public/index.html` is the page.
- `public/events.js` holds the listings. It is generated; do not edit by hand.
- `update_site.py` turns `../fr33_collector/out/events.json` into `public/events.js`.
- `build_db.py` holds the shared cleanup rules (borough, category, canceled-event filter, text cleanup).
  Its `main()` is an older pipeline for a different host and is not used by this site.

Refresh: run the collector, then `python website/update_site.py` from the project folder.
Online, the GitHub Action in `.github/workflows/refresh-events.yml` does this twice a day.
