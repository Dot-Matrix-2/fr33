#!/usr/bin/env python3
"""fr33 NYC free-events collector.

Pulls upcoming events from structured sources, keeps ONLY events that are
strictly $0, dedupes, and writes events.json + events.csv.

Usage:  python collect.py [--days 31] [--out ./out]
Needs:  pip install requests icalendar python-dateutil
Network: outbound HTTPS to the hosts in SOURCES must be allowed.
"""
import argparse, csv, hashlib, html, json, os, re, sys
from datetime import datetime, timedelta, date, timezone
import requests
from dateutil import parser as dtp, tz

NYC = tz.gettz("America/New_York")

UA = {"User-Agent": "fr33-nyc-events/0.1 (info-only free events index)"}
TIMEOUT = 30

# ---------------------------------------------------------------- free filter
# STRICT policy: $0 only. Suggested donation, pay-what-you-wish, drink/food
# minimums, ticket-with-purchase => NOT free.
NOT_FREE = re.compile(
    r"suggested donation|pay[- ]what[- ]you[- ](wish|can)|donation (requested|encouraged)|"
    r"\bminimum\b|cover charge|\bcover\b(?! free)|\$\s?\d|tickets? (start|from)|"
    r"book (is )?included|includes? (a )?(book|drink)|admission fee|fee applies|"
    r"sliding scale|members only|\bmember(s)? (only|free)\b|cuid|nyu (id|students only)|"
    r"students? only|registration fee|materials fee", re.I)
FREE = re.compile(
    r"\bfree\b|no cost|no charge|complimentary|free admission|free of charge|\$\s?0\b", re.I)
RSVP = re.compile(
    r"\brsvp\b|register|registration|reserve|sign[- ]?up|free tickets?|eventbrite|"
    r"advance (booking|reservation)", re.I)

def classify(*texts):
    """Return (is_free, rsvp_required, evidence). Conservative: must say free AND
    show no paid signal."""
    blob = " ".join(t for t in texts if t)
    if not blob:
        return False, False, ""
    m = FREE.search(blob)
    if not m or NOT_FREE.search(blob):
        return False, False, ""
    s = max(0, m.start() - 40)
    needs = bool(RSVP.search(re.sub(r"\b(no|not|without)\s+(rsvp|registration|reservations?|sign[- ]?up)( (is )?(required|needed))?", "", blob, flags=re.I)))
    return True, needs, blob[s:m.end() + 40].strip()

# ---------------------------------------------------------------- cleanup
def clean_text(s, n=None):
    """Strip HTML tags, decode entities (&#8217; -> ’), collapse whitespace."""
    if s is None:
        return None
    s = html.unescape(re.sub(r"<[^>]+>", " ", str(s))).replace("\u00a0", " ")
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\s*Learn More\s*$", "", s).strip()
    if n and len(s) > n:
        s = s[:n].rsplit(" ", 1)[0] + "…"
    return s or None

def iso_dt(s):
    """Any source date/time -> 'YYYY-MM-DDTHH:MM:SS' in New York local time."""
    if not s:
        return None
    try:
        d = dtp.parse(str(s).strip().lstrip("T"))
    except (ValueError, OverflowError):
        return None
    if d.tzinfo:
        d = d.astimezone(NYC).replace(tzinfo=None)
    return d.isoformat(timespec="seconds")

def num(x):
    try:
        return round(float(x), 6)
    except (TypeError, ValueError):
        return None

BOROUGH_CODES = {"M": "Manhattan", "B": "Brooklyn", "Q": "Queens", "X": "Bronx", "R": "Staten Island"}
BOROUGH_WORDS = {"manhattan": "Manhattan", "brooklyn": "Brooklyn", "queens": "Queens",
                 "bronx": "Bronx", "staten island": "Staten Island"}
# Coarse Manhattan outline (lat, lon) and Brooklyn/Queens boundary line for a geo guess.
MANHATTAN = [(40.7003, -74.0197), (40.7110, -73.9760), (40.7420, -73.9710), (40.7760, -73.9420),
             (40.7960, -73.9270), (40.8080, -73.9330), (40.8350, -73.9340), (40.8730, -73.9110),
             (40.8790, -73.9260), (40.8200, -73.9590), (40.7570, -74.0090), (40.7020, -74.0200)]
BK_QN = [(40.6400, -73.8550), (40.6830, -73.8680), (40.6950, -73.8960), (40.7130, -73.9210), (40.7390, -73.9620)]

def _inside(pt, poly):
    y, x = pt; c = False
    for i in range(len(poly)):
        y1, x1 = poly[i]; y2, x2 = poly[i - 1]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            c = not c
    return c

def borough_from_geo(lat, lon):
    if lat is None or lon is None or not (40.45 < lat < 40.95 and -74.3 < lon < -73.65):
        return None
    if lon < -74.05 and lat < 40.66:
        return "Staten Island"
    if _inside((lat, lon), MANHATTAN):
        return "Manhattan"
    if lat > 40.785 and lon > -73.935:
        return "Bronx" if lat > 40.80 or lon < -73.90 else "Queens"
    if lat < 40.61 and lon > -73.94:
        return "Queens"  # Rockaways
    edge = BK_QN[0][1] if lat <= BK_QN[0][0] else BK_QN[-1][1]
    for (a_lat, a_lon), (b_lat, b_lon) in zip(BK_QN, BK_QN[1:]):
        if a_lat <= lat <= b_lat:
            edge = a_lon + (b_lon - a_lon) * (lat - a_lat) / (b_lat - a_lat)
    return "Brooklyn" if lon < edge else "Queens"

# Places with no coordinates in the source data.
PLACE_HINTS = {"high line": "Manhattan", "john golden park": "Queens"}

def place_from_title(title):
    """'It's My Park at Marine Park' -> 'Marine Park'; 'Kids In Motion: Nostrand Playground' -> 'Nostrand Playground'."""
    m = re.search(r"(?:\bat |: )([A-Z][^|:]{2,80})$", title or "")
    return m.group(1).strip() if m else None

def borough_for(borough, parkid, lat, lon, address):
    """Source value -> NYC Parks park-ID prefix (M/B/Q/X/R) -> coordinates -> address text."""
    b = BOROUGH_WORDS.get((borough or "").strip().lower())
    if b:
        return b
    b = BOROUGH_CODES.get((parkid or "")[:1].upper())
    if b:
        return b
    b = borough_from_geo(lat, lon)
    if b:
        return b
    a = (address or "").lower()
    return next((v for k, v in list(BOROUGH_WORDS.items()) + list(PLACE_HINTS.items()) if k in a), None)

# ---------------------------------------------------------------- schema
FIELDS = ["id", "title", "start", "end", "all_day", "venue", "address", "borough",
          "lat", "lon", "category", "description", "url", "rsvp_required",
          "free_evidence", "source", "source_url", "fetched_at"]

def mk(**k):
    """Build one clean event record. All cleanup happens here so every source is consistent."""
    for f in ("title", "venue", "address", "category", "free_evidence", "source"):
        k[f] = clean_text(k.get(f))
    k["description"] = clean_text(k.get("description"), 500)
    k["start"], k["end"] = iso_dt(k.get("start")), iso_dt(k.get("end"))
    k["lat"], k["lon"] = num(k.get("lat")), num(k.get("lon"))
    if (k.get("venue") or "").lower() in BOROUGH_WORDS:  # some sources put the borough in the venue field
        k["borough"] = k["borough"] or k["venue"]
        k["venue"] = None
    if not k.get("venue"):
        k["venue"] = place_from_title(k.get("title"))
    k["borough"] = borough_for(k.get("borough"), k.pop("parkid", None), k["lat"], k["lon"],
                              " ".join(x for x in (k["address"], k["venue"]) if x))
    k["all_day"] = bool(k.get("all_day"))
    k["rsvp_required"] = bool(k.get("rsvp_required"))
    e = {f: k.get(f) for f in FIELDS}
    key = f"{(e['title'] or '').lower().strip()}|{(e['start'] or '')[:16]}|{(e['venue'] or '').lower()[:30]}"
    e["id"] = hashlib.sha1(key.encode()).hexdigest()[:12]
    e["fetched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return e

def get(url, **kw):
    r = requests.get(url, headers=UA, timeout=TIMEOUT, **kw)
    r.raise_for_status()
    return r

# ---------------------------------------------------------------- adapters
def parks_dt(d, t):
    """NYC Parks sometimes puts the full timestamp in starttime and leaves startdate empty."""
    d, t = (d or "")[:10], (t or "").strip()
    if re.match(r"\d{4}-\d{2}-\d{2}", t):
        return t
    return f"{d}T{t}" if d and t else d

def nyc_parks(win):
    """NYC Open Data w3wp-dpdi (rolling next 14 days). Has no price field, so
    free status comes from text; requires explicit 'free' wording."""
    out, off = [], 0
    while True:
        rows = get("https://data.cityofnewyork.us/resource/w3wp-dpdi.json",
                   params={"$limit": 1000, "$offset": off, "$order": "guid"}).json()
        if not rows: break
        for r in rows:
            ok, rsvp, ev = classify(r.get("title"), r.get("description"),
                                    r.get("registration_description"))
            if not ok: continue
            coords = [c for c in re.split(r"[,\s]+", r.get("coordinates") or "") if c]
            out.append(mk(
                title=r.get("title"),
                start=parks_dt(r.get("startdate"), r.get("starttime")),
                end=parks_dt(r.get("enddate"), r.get("endtime")),
                parkid=(r.get("parkids") or "").split(",")[0],
                venue=r.get("parknames"), address=r.get("location"),
                lat=coords[0].strip() if len(coords) == 2 else None,
                lon=coords[1].strip() if len(coords) == 2 else None,
                category=r.get("categories"), description=r.get("description"),
                url=(r.get("link") or {}).get("url") if isinstance(r.get("link"), dict) else r.get("link"),
                rsvp_required=rsvp or bool(r.get("registration_url")),
                free_evidence=ev, source="NYC Parks", source_url="https://www.nycgovparks.org/events"))
        off += 1000
    return out

def tribe(name, base, borough=None, default_venue=None):
    """The Events Calendar (WordPress) REST API."""
    def run(win):
        out, page = [], 1
        while True:
            try:
                j = get(f"{base}/wp-json/tribe/events/v1/events", params={
                    "per_page": 50, "page": page,
                    "start_date": win[0].isoformat(), "end_date": win[1].isoformat()}).json()
            except requests.HTTPError:
                break
            for ev in j.get("events", []):
                cost = (ev.get("cost") or "").strip()
                desc = re.sub("<[^>]+>", " ", ev.get("description") or "")
                # Tribe 'cost' field is authoritative when present.
                if cost and not FREE.search(cost):
                    continue
                ok, rsvp, evd = classify(cost, ev.get("title"), desc)
                if not ok: continue
                v = ev.get("venue") or {}
                if isinstance(v, list):
                    v = v[0] if v else {}
                addr = ", ".join(x for x in (v.get("address"), v.get("city")) if x) or None
                out.append(mk(title=ev.get("title"), start=ev.get("start_date"),
                              end=ev.get("end_date"), all_day=ev.get("all_day"),
                              venue=v.get("venue") or default_venue, address=addr,
                              borough=borough, lat=v.get("geo_lat"), lon=v.get("geo_lng"),
                              description=desc, url=ev.get("url"),
                              rsvp_required=rsvp, free_evidence=evd or cost,
                              source=name, source_url=base))
            if not j.get("next_rest_url"): break
            page += 1
        return out
    return run

def jsonld(name, url, borough=None):
    """Generic schema.org/Event JSON-LD scraper for a listing URL."""
    def run(win):
        html = get(url).text
        out = []
        for blob in re.findall(r'<script[^>]+ld\+json[^>]*>(.*?)</script>', html, re.S):
            try: data = json.loads(blob)
            except ValueError: continue
            for d in (data if isinstance(data, list) else data.get("@graph", [data])):
                if d.get("@type") not in ("Event", "MusicEvent", "TheaterEvent", "EducationEvent"):
                    continue
                offers = d.get("offers") or {}
                offers = offers[0] if isinstance(offers, list) and offers else offers
                price = str(offers.get("price", "")) if isinstance(offers, dict) else ""
                if price not in ("0", "0.0", "0.00"):
                    continue  # JSON-LD must state price 0; otherwise skip
                loc = d.get("location") or {}
                loc = loc[0] if isinstance(loc, list) and loc else loc
                out.append(mk(title=d.get("name"), start=d.get("startDate"), end=d.get("endDate"),
                              venue=(loc.get("name") if isinstance(loc, dict) else None),
                              address=(loc.get("address") or {}).get("streetAddress") if isinstance(loc, dict) and isinstance(loc.get("address"), dict) else None,
                              borough=borough, description=(d.get("description") or "")[:500],
                              url=d.get("url") or url, rsvp_required=False,
                              free_evidence="JSON-LD offers.price=0", source=name, source_url=url))
        return out
    return run

# Add sources here. Only endpoints that respond are used; failures are logged.
SOURCES = [
    ("NYC Parks", nyc_parks),
    ("City Parks Foundation", tribe("City Parks Foundation", "https://cityparksfoundation.org")),
    ("Prospect Park Alliance", tribe("Prospect Park Alliance", "https://www.prospectpark.org", "Brooklyn", "Prospect Park")),
    ("Riverside Park Conservancy", tribe("Riverside Park Conservancy", "https://riversideparknyc.org", "Manhattan", "Riverside Park")),
    ("The High Line", jsonld("The High Line", "https://www.thehighline.org/events/", "Manhattan")),
    ("Times Square Alliance", jsonld("Times Square Alliance", "https://www.timessquarenyc.org/events", "Manhattan")),
    # Tribe endpoint returned 404 on 2026-09-30 (need another route): BRIC, Brooklyn Bridge Park, Hudson River Park,
    # Downtown Brooklyn, Brookfield Place, Radegast. Squarespace ?format=json and Localist /api/2 also failed/blocked.
    # TODO: Bryant Park (find XHR JSON), NYPL/BPL/QPL, Lincoln Center, Juilliard, bookstores.
]

# ---------------------------------------------------------------- main
def in_window(e, win):
    try:
        d = dtp.parse(e["start"]).date()
    except (TypeError, ValueError):
        return False
    return win[0] <= d <= win[1]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=31)
    ap.add_argument("--out", default="out")
    ap.add_argument("--reprocess", metavar="EVENTS_JSON",
                    help="re-apply cleanup to a saved events.json instead of fetching")
    a = ap.parse_args()
    today = date.today()
    win = (today, today + timedelta(days=a.days))
    events, log, old = {}, [], {}
    sources = SOURCES
    if a.reprocess:
        old = json.load(open(a.reprocess, encoding="utf-8"))
        log = old.get("source_log", [])
        fixed_venue = {"Prospect Park Alliance": "Prospect Park", "Riverside Park Conservancy": "Riverside Park"}
        for e in old.get("events", []):
            f = {k: v for k, v in e.items() if k != "id"}
            if f.get("venue") == f.get("source"):  # old builds used the organization as venue
                f["venue"] = fixed_venue.get(f.get("source"))
            n = mk(**f); n["fetched_at"] = e.get("fetched_at")
            if in_window(n, win):
                events.setdefault(n["id"], n)
        sources = []
    for name, fn in sources:
        try:
            got = [e for e in fn(win) if in_window(e, win)]
            log.append({"source": name, "status": "ok", "kept": len(got)})
            for e in got: events.setdefault(e["id"], e)
        except Exception as ex:
            log.append({"source": name, "status": f"error: {ex}", "kept": 0})
            print(f"[warn] {name}: {ex}", file=sys.stderr)
    rows = sorted(events.values(), key=lambda e: e["start"] or "")
    os.makedirs(a.out, exist_ok=True)
    generated = old.get("generated_at") if a.reprocess else None  # keep the real fetch time
    json.dump({"generated_at": generated or datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "window": [win[0].isoformat(), win[1].isoformat()],
               "policy": "strict $0 only; RSVP-free flagged via rsvp_required",
               "source_log": log, "events": rows},
              open(f"{a.out}/events.json", "w", encoding="utf-8"), indent=2)
    with open(f"{a.out}/events.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, FIELDS); w.writeheader(); w.writerows(rows)
    print(f"{len(rows)} free events -> {a.out}/  ({sum(l['status']=='ok' for l in log)}/{len(log)} sources ok)")

if __name__ == "__main__":
    main()
