"""
One-time pull of Home Front Command alert history into data/.

Runs locally, not in the container, and only from an Israeli IP (the API is
geo-blocked). The container loads the committed output, never the live API.

    python3 scripts/pull_alerts.py
"""

import gzip
import hashlib
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

START_DATE = datetime(2023, 10, 7)
END_DATE = datetime(2026, 9, 10)

HISTORY_URL = "https://alerts-history.oref.org.il/Shared/Ajax/GetAlarmsHistory.aspx"
GETDISTRICTS_URL = "https://alerts-history.oref.org.il/Shared/Ajax/GetDistricts.aspx"
DISTRICTS_URL = "https://www.oref.org.il/districts/districts_{lang}.json"
CITIES_URL = "https://www.tzevaadom.co.il/static/cities.json"

RESPONSE_CAP = 3000  # the history endpoint silently truncates at this many rows
RETRIES = 3
PAUSE_SECONDS = 0.4

DATA_DIR = Path("data")
CACHE_DIR = DATA_DIR / "_cache"
ZONE_CACHE = DATA_DIR / "_cache_zones"
OUTPUT_RAW = DATA_DIR / "alerts_raw.json.gz"
OUTPUT_CLEAN = DATA_DIR / "alerts_clean.jsonl.gz"

HEADERS = {
    "Referer": "https://www.oref.org.il/",
    "X-Requested-With": "XMLHttpRequest",
    "User-Agent": "Mozilla/5.0 (one-time historical pull)",
}

ACTIONABLE_CATEGORIES = {1, 2, 4, 7, 10}
ALL_CLEAR_MARKERS = ("האירוע הסתיים", "הוסר החשש", "החשש הוסר", "ניתן לצאת",
                     "הנחיות לחזרה")
PRE_ALERT_MARKER = "בדקות הקרובות"
SHELTER_MARKER = "התקרבו למרחב מוגן"


def get_json(url, params=None):
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=60)
            r.raise_for_status()
            body = r.content.decode("utf-8-sig")  # oref.org.il prefixes a BOM
            if not body.strip():
                return []
            return json.loads(body)
        except Exception as e:
            last = e
            if attempt < RETRIES:
                print(f"    attempt {attempt} failed ({e}); retrying")
                time.sleep(2 ** attempt)
    raise RuntimeError(f"{url} failed after {RETRIES} attempts: {last}")


def write_gz(path, obj):
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


def write_jsonl_gz(path, docs):
    with gzip.open(path, "wt", encoding="utf-8") as f:
        for d in docs:
            f.write(json.dumps(d, ensure_ascii=False, separators=(",", ":")))
            f.write("\n")


def reference(name, url, params=None):
    # Reuse the saved snapshot so re-running normalisation is reproducible.
    path = DATA_DIR / f"{name}.json.gz"
    if path.exists():
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)
    obj = get_json(url, params)
    write_gz(path, obj)
    return obj


def load_references():
    return {
        "gd_he": reference("ref_getdistricts_he", GETDISTRICTS_URL, {"lang": "he"}),
        "gd_en": reference("ref_getdistricts_en", GETDISTRICTS_URL, {"lang": "en"}),
        "dist_he": reference("ref_districts_he", DISTRICTS_URL.format(lang="heb")),
        "dist_en": reference("ref_districts_en", DISTRICTS_URL.format(lang="eng")),
        "tzevaadom": reference("ref_tzevaadom_cities", CITIES_URL),
    }


def polygons_to_query():
    refs = load_references()
    names = set(refs["tzevaadom"].get("cities", {}))
    names.update(d["label"] for d in refs["gd_he"])
    # Old names only appear in historical responses.
    for f in CACHE_DIR.glob("*.json"):
        for r in json.loads(f.read_text(encoding="utf-8")):
            if r.get("data"):
                names.add(r["data"])
    return sorted(names), refs


def fetch_polygon(zone, start, end):
    # One polygon over the whole window stays far below the 3000-row cap.
    path = ZONE_CACHE / (hashlib.sha1(zone.encode("utf-8")).hexdigest() + ".json")
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8")), True
    batch = get_json(HISTORY_URL, {
        "lang": "he",
        "mode": 0,
        "fromDate": start.strftime("%d.%m.%Y"),
        "toDate": end.strftime("%d.%m.%Y"),
        "city_0": zone,
    })
    batch = batch if isinstance(batch, list) else []
    path.write_text(json.dumps(batch, ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8")
    return batch, False


def fetch_history(start, end):
    ZONE_CACHE.mkdir(parents=True, exist_ok=True)
    zones, refs = polygons_to_query()
    print(f"Fetching {start.date()} -> {end.date()} across {len(zones)} polygons")

    lo, hi = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
    seen, records, capped, hits = set(), [], [], 0

    for i, zone in enumerate(zones, 1):
        batch, cached = fetch_polygon(zone, start, end)
        hits += cached
        if len(batch) >= RESPONSE_CAP:
            capped.append(zone)
            print(f"  [{i}/{len(zones)}] {zone}: AT CAP — truncated")

        for r in batch:
            if r.get("data") != zone:
                continue
            if not (lo <= r.get("alertDate", "")[:10] <= hi):
                continue
            key = (r.get("rid"), r.get("data"))
            if key in seen:
                continue
            seen.add(key)
            records.append(r)

        if i % 100 == 0:
            print(f"  [{i}/{len(zones)}] {len(records)} records so far", flush=True)
        if not cached:
            time.sleep(PAUSE_SECONDS)

    print(f"\n  {hits} polygon(s) from cache, {len(zones) - hits} fetched")
    if capped:
        print(f"  WARNING: {len(capped)} polygon(s) hit the {RESPONSE_CAP} ceiling")

    recovered = merge_day_cache(records, seen, lo, hi)
    if recovered:
        print(f"  {recovered} record(s) recovered from the per-day cache")
    return records, capped, refs


def merge_day_cache(records, seen, lo, hi):
    # city_0 only resolves current polygon names; retired ones are only
    # reachable through whole-day responses.
    added = 0
    for f in sorted(CACHE_DIR.glob("*.json")):
        day = f.stem
        try:
            batch = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if len(batch) >= RESPONSE_CAP:
            continue  # truncated, and the endpoint misdates rows on these days
        for r in batch:
            if r.get("alertDate", "")[:10] != day:
                continue
            if not (lo <= day <= hi):
                continue
            key = (r.get("rid"), r.get("data"))
            if key in seen or r.get("rid") is None or not r.get("data"):
                continue
            seen.add(key)
            records.append(r)
            added += 1
    return added


def classify(category, desc):
    d = desc or ""
    # All-clear first: "ירי רקטות וטילים - האירוע הסתיים" also contains the rocket wording.
    if any(m in d for m in ALL_CLEAR_MARKERS):
        return "all_clear"
    if PRE_ALERT_MARKER in d:
        return "pre_alert"
    if category in ACTIONABLE_CATEGORIES or SHELTER_MARKER in d:
        return "alert"
    return "info"


def city_cluster(zone_name):
    return zone_name.split(" - ")[0].strip() if " - " in zone_name else zone_name


def normalize(raw, refs):
    gd_he = {d["label"]: d for d in refs["gd_he"]}
    gd_en = {d["id"]: d for d in refs["gd_en"]}
    rashut_he = {d["label"]: d.get("rashut") for d in refs["dist_he"]}
    rashut_en = {d["id"]: d.get("rashut") for d in refs["dist_en"]}
    tz = refs["tzevaadom"].get("cities", {})

    cleaned, unmatched = [], set()
    for r in raw:
        zone = r.get("data")
        off = gd_he.get(zone)
        en = gd_en.get(off["id"]) if off else None
        geo = tz.get(zone)
        if off is None:
            unmatched.add(zone)

        zone_en = (en or {}).get("label") or (geo or {}).get("en")
        city = city_cluster(zone)
        city_en = city_cluster(zone_en) if zone_en else None
        ts = r.get("alertDate")

        cleaned.append({
            # rid identifies a barrage and repeats across polygons; rid:zone is unique.
            "_id": f"{r.get('rid')}:{zone}",
            "alert_id": r.get("rid"),
            "event_id": f"{ts}|{r.get('category')}",
            "category": r.get("category"),
            "category_desc": r.get("category_desc"),
            "message_type": classify(r.get("category"), r.get("category_desc")),
            "zone": zone,
            "zone_en": zone_en,
            "zone_id": off["id"] if off else None,
            "city": city,
            "city_en": city_en,
            "municipality": rashut_he.get(zone) or city,
            "municipality_en": (rashut_en.get(off["id"]) if off else None) or city_en,
            "region": off["areaname"] if off else None,
            "region_en": en["areaname"] if en else None,
            "shelter_time_seconds": off["migun_time"] if off else None,
            "timestamp": ts,
            "lat": geo.get("lat") if geo else None,
            "lng": geo.get("lng") if geo else None,
            "source": "oref_alerts_history_v3",
        })
    return cleaned, unmatched


def main():
    DATA_DIR.mkdir(exist_ok=True)
    try:
        raw, capped, refs = fetch_history(START_DATE, END_DATE)
    except Exception as e:
        sys.exit(f"Fetch failed: {e}\n(the API only answers Israeli IPs)")

    (DATA_DIR / "excluded_polygons.json").write_text(json.dumps({
        "reason": f"response hit the {RESPONSE_CAP}-row cap and may be truncated",
        "count": len(capped),
        "polygons": capped,
    }, indent=2), encoding="utf-8")

    write_gz(OUTPUT_RAW, raw)
    print(f"\nRaw dump written to {OUTPUT_RAW} ({len(raw)} records)")

    cleaned, unmatched = normalize(raw, refs)
    write_jsonl_gz(OUTPUT_CLEAN, cleaned)
    print(f"Normalized {len(cleaned)} record(s) written to {OUTPUT_CLEAN}")
    print(f"{len(capped)} truncated polygon(s) listed in data/excluded_polygons.json")

    if unmatched:
        print(f"{len(unmatched)} polygon name(s) not in the official registry "
              f"(region and shelter time left null)")


if __name__ == "__main__":
    main()
