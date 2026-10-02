#!/usr/bin/env python3
"""Turn collect.py's events.json into Free in NYC page documents.

Usage: python build_db.py out/events.json versions.json outdir
  versions.json: {"days": {"<doc_id>": <version>, ...}, "meta": {"status": <version>}}
                 (versions of documents that already exist; {} on the first run)
Writes outdir/docs/*.json (one file per document) and outdir/plan.json:
  {"batches": [[<ArtifactData batch write entry>, ...], ...], "summary": {...}}
Each day in the window is one document in collection "days" (id = YYYY-MM-DD).
Past day documents are deleted. meta/status records when the data was refreshed.
"""
import html, json, os, re, sys
from datetime import date, datetime, timedelta

MAX_DESC = 300
BATCH_BYTES = 700_000   # stay well under the 1 MiB batch limit
BATCH_MAX = 50

CAT_RULES = [  # first match wins
    ("family", r"best for kids|\bkids?\b|children|family|families|storytime|teen"),
    ("music", r"concert|music|jazz|band|orchestra|choir|dj\b|piano|opera|singing"),
    ("film", r"film|movie|cinema|screening|theat(er|re)|shakespeare|dance perf|performance|comedy|circus"),
    ("food", r"food|market|greenmarket|tasting|cook|harvest festival"),
    ("arts", r"\bart\b|arts|exhibit|gallery|museum|sculpture|history|mural|craft"),
    ("outdoors", r"fitness|yoga|pilates|nature|hik|walk|run\b|running|sport|bike|cycl|kayak|garden|bird|tree|outdoor|volunteer|cleanup|exercise|swim|tennis|soccer|basketball"),
    ("talks", r"talk|lecture|book|reading|author|workshop|tour|class|education|community|panel|discussion"),
]

BOROUGH_CODES = {"M": "Manhattan", "B": "Brooklyn", "Q": "Queens", "X": "Bronx", "R": "Staten Island"}
BOROUGH_WORDS = {"manhattan": "Manhattan", "brooklyn": "Brooklyn", "queens": "Queens",
                 "bronx": "Bronx", "staten island": "Staten Island"}

# Coarse Manhattan outline (lat, lon) for a point-in-polygon borough guess.
MANHATTAN = [(40.7003, -74.0197), (40.7110, -73.9760), (40.7420, -73.9710), (40.7760, -73.9420),
             (40.7960, -73.9270), (40.8080, -73.9330), (40.8350, -73.9340), (40.8730, -73.9110),
             (40.8790, -73.9260), (40.8200, -73.9590), (40.7570, -74.0090), (40.7020, -74.0200)]
# Brooklyn/Queens boundary, west side of this line is Brooklyn.
BK_QN = [(40.7390, -73.9620), (40.7130, -73.9210), (40.6950, -73.8960), (40.6830, -73.8680), (40.6400, -73.8550)]


def inside(pt, poly):
    y, x = pt; c = False
    for i in range(len(poly)):
        y1, x1 = poly[i]; y2, x2 = poly[i - 1]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            c = not c
    return c


def borough_from_geo(lat, lon):
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return ""
    if not (40.45 < lat < 40.95 and -74.3 < lon < -73.65):
        return ""
    if lon < -74.05 and lat < 40.66:
        return "Staten Island"
    if inside((lat, lon), MANHATTAN):
        return "Manhattan"
    if lat > 40.785 and lon > -73.935:
        return "Bronx" if lat > 40.80 or lon < -73.90 else "Queens"
    if lat < 40.61 and lon > -73.94:
        return "Queens"  # Rockaways
    # Brooklyn vs Queens: interpolate the boundary longitude at this latitude.
    pts = sorted(BK_QN)
    if lat <= pts[0][0]:
        edge = pts[0][1]
    elif lat >= pts[-1][0]:
        edge = pts[-1][1]
    else:
        for (a_lat, a_lon), (b_lat, b_lon) in zip(pts, pts[1:]):
            if a_lat <= lat <= b_lat:
                edge = a_lon + (b_lon - a_lon) * (lat - a_lat) / (b_lat - a_lat)
                break
    return "Brooklyn" if lon < edge else "Queens"


def borough_for(e):
    b = (e.get("borough") or "").strip()
    for k, v in BOROUGH_WORDS.items():
        if b.lower() == k:
            return v
    pid = (e.get("parkids") or "")[:1].upper()
    if pid in BOROUGH_CODES:
        return BOROUGH_CODES[pid]
    g = borough_from_geo(e.get("lat"), e.get("lon"))
    if g:
        return g
    addr = (e.get("address") or "").lower()
    for k, v in BOROUGH_WORDS.items():
        if k in addr:
            return v
    return ""


def category_for(e):
    text = " ".join(str(e.get(k) or "") for k in ("category", "title")).lower()
    for cat, rx in CAT_RULES:
        if re.search(rx, text):
            return cat
    text += " " + (e.get("description") or "").lower()[:200]
    for cat, rx in CAT_RULES:
        if re.search(rx, text):
            return cat
    return "talks"


def split_dt(s):
    """'2026-10-01T07:30:00.000' or '2026-10-01 18:00:00' -> ('2026-10-01', '07:30')"""
    if not s:
        return "", ""
    m = re.search(r"(\d{4}-\d{2}-\d{2})(?:[T ](\d{2}):(\d{2}))?", str(s))
    if not m:
        return "", ""
    return m.group(1), (f"{m.group(2)}:{m.group(3)}" if m.group(2) else "")


def clean(s, n=None):
    s = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", str(s or "")))).strip()
    s = re.sub(r"\s*Learn More\s*", " ", s).strip()
    if n and len(s) > n:
        s = s[:n].rsplit(" ", 1)[0] + "…"
    return s


def to_event(e):
    d, st = split_dt(e.get("start"))
    ed, et = split_dt(e.get("end"))
    if not d or not e.get("title"):
        return None
    if re.match(r"\s*(cancel+ed|postponed)\b", str(e.get("title")), re.I):
        return None
    all_day = bool(e.get("all_day")) or (st == "00:00" and et in ("", "00:00", "23:59"))
    url = e.get("url") or e.get("source_url") or ""
    return {
        "id": e.get("id") or "", "title": clean(e.get("title"), 160), "date": d,
        "start_time": "" if all_day else st,
        "end_time": "" if all_day or (ed and ed != d) else et,
        "venue": clean(e.get("venue"), 120), "address": clean(e.get("address"), 160),
        "borough": borough_for(e), "category": category_for(e),
        "description": clean(e.get("description"), MAX_DESC),
        "url": url if re.match(r"https?://", url or "") else "",
        "rsvp_required": bool(e.get("rsvp_required")),
        "source": clean(e.get("source"), 60),
    }


def main():
    src, vpath, outdir = sys.argv[1], sys.argv[2], sys.argv[3]
    data = json.load(open(src))
    try:
        versions = json.load(open(vpath))
    except (OSError, ValueError):
        versions = {}
    vdays = versions.get("days", {}) or {}
    vmeta = (versions.get("meta", {}) or {}).get("status")

    start, end = (date.fromisoformat(x) for x in data.get("window") or
                  [date.today().isoformat(), (date.today() + timedelta(days=31)).isoformat()])
    today = date.today()
    start = max(start, today)

    by_day, seen = {}, set()
    for raw in data.get("events", []):
        ev = to_event(raw)
        if not ev or ev["id"] in seen:
            continue
        seen.add(ev["id"])
        dd = date.fromisoformat(ev["date"])
        if start <= dd <= end:
            by_day.setdefault(ev["date"], []).append(ev)

    os.makedirs(f"{outdir}/docs", exist_ok=True)
    writes = []
    d = start
    while d <= end:
        k = d.isoformat()
        evs = sorted(by_day.get(k, []), key=lambda x: (x["start_time"] or "", x["title"]))
        path = os.path.abspath(f"{outdir}/docs/day-{k}.json")
        json.dump({"date": k, "count": len(evs), "events": evs}, open(path, "w"), ensure_ascii=False)
        w = {"op": "set", "collection": "days", "doc_id": k, "file_path": path}
        if k in vdays:
            w["if_version"] = int(vdays[k])
        writes.append(w)
        d += timedelta(days=1)
    for k, ver in vdays.items():  # delete past days and anything outside the window
        try:
            kd = date.fromisoformat(k)
        except ValueError:
            kd = None
        if kd is None or kd < start or kd > end:
            writes.append({"op": "delete", "collection": "days", "doc_id": k, "if_version": int(ver)})

    total = sum(len(v) for v in by_day.values())
    ok = [l["source"] for l in data.get("source_log", []) if l.get("status") == "ok"]
    bad = [l["source"] for l in data.get("source_log", []) if l.get("status") != "ok"]
    meta_path = os.path.abspath(f"{outdir}/docs/meta-status.json")
    json.dump({"updated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
               "generated_at": data.get("generated_at"), "window": [start.isoformat(), end.isoformat()],
               "event_count": total, "sources_ok": ok, "sources_failed": bad},
              open(meta_path, "w"), ensure_ascii=False)
    mw = {"op": "set", "collection": "meta", "doc_id": "status", "file_path": meta_path}
    if vmeta:
        mw["if_version"] = int(vmeta)
    writes.append(mw)  # last, so the page only reports "updated" after the days are in

    batches, cur, size = [], [], 0
    for w in writes:
        sz = os.path.getsize(w["file_path"]) if "file_path" in w else 100
        if cur and (len(cur) >= BATCH_MAX or size + sz > BATCH_BYTES):
            batches.append(cur); cur, size = [], 0
        cur.append(w); size += sz
    if cur:
        batches.append(cur)
    plan = {"batches": batches, "summary": {"events": total, "days": len(by_day),
            "writes": len(writes), "batches": len(batches), "sources_ok": ok, "sources_failed": bad}}
    json.dump(plan, open(f"{outdir}/plan.json", "w"), indent=1)
    print(json.dumps(plan["summary"]))


if __name__ == "__main__":
    main()
