# fr33 NYC free-events dataset

`collect.py` builds `events.json` (+ `events.csv`) for the UI. Window: today through +31 days.

## Free policy (strict)
An event is kept only if its own text/price field says free AND shows no paid signal.
Excluded: suggested donation, pay-what-you-wish, drink/food minimums, cover, book/ticket
included with purchase, members-only, CUID/NYU-only, sliding scale.
Free events needing a free RSVP/ticket are kept with `rsvp_required = true`.
When unsure, the event is dropped.

## events.json
Top level: `generated_at`, `window`, `policy`, `source_log` (per-source ok/error + count), `events[]`.

| field | notes |
|---|---|
| id | stable hash of title+start+venue (dedupe key) |
| title, description | description truncated to 500 chars |
| start, end | ISO local time; `all_day` bool |
| venue, address, borough, lat, lon | borough may be null |
| category | source category text |
| url | event page |
| rsvp_required | true if free but sign-up/free ticket needed |
| free_evidence | the text snippet that justified "free" |
| source, source_url | provenance |
| fetched_at | UTC |

## Cleanup applied to every event (in `mk()`)
- HTML tags removed and entities decoded (`&#8217;` -> ’); whitespace collapsed; descriptions capped at 500 chars.
- `start`/`end` always `YYYY-MM-DDTHH:MM:SS`, New York local time.
- `borough` from the source, else the NYC Parks park ID prefix, else coordinates, else address/venue text.
- `venue` is the actual place, never the organizing group. When a source gives only a borough,
  the place is taken from the title ("It's My Park at Marine Park" -> Marine Park).

## Run
`pip install requests icalendar python-dateutil && python collect.py --days 31`

Re-clean a saved file without fetching: `python collect.py --reprocess out/events.json`

Needs outbound HTTPS to the hosts in `SOURCES`. NYC Parks (`data.cityofnewyork.us`) is the
highest-value source. Check `source_log` after each run: zero-event seasonal sources
(Governors Island, Shakespeare in the Park) are normal, not failures.

## Status
Sources wired: NYC Parks (Socrata), 8 WordPress/Tribe sites, 2 JSON-LD sites. Not yet wired:
Bryant Park, NYPL/BPL/QPL, Lincoln Center, Juilliard, bookstores, universities (each needs
endpoint discovery). Discovery-only sites (The Skint, NYC For Free, See Saw) are never republished.
Last live run: 2026-09-30 (656 events; 553 still upcoming as of 2026-10-02).
