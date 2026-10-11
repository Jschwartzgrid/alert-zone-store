"""Checks the dataset file and the loaded database. Run with the database up."""

import gzip
import json
from collections import Counter
from pathlib import Path

from pymongo import MongoClient
from pymongo.errors import WriteError

with gzip.open(Path("data/alerts_clean.jsonl.gz"), "rt", encoding="utf-8") as f:
    docs = [json.loads(line) for line in f if line.strip()]

ids = Counter(d["_id"] for d in docs)
assert len(ids) == len(docs), f"duplicate _id: {[k for k, v in ids.items() if v > 1][:5]}"
assert len({d["alert_id"] for d in docs}) < len(docs), "rid unexpectedly unique"

joined = sum(1 for d in docs if d["region"] is not None)
assert joined / len(docs) > 0.99, f"join rate {joined / len(docs):.1%}"
assert all(d["municipality"] for d in docs), "row without a municipality"
regions = {d["region_en"] for d in docs if d["region_en"]}
assert len(regions) == 33, f"expected 33 official regions, got {len(regions)}"

per_zone = Counter(d["zone"] for d in docs)
per_day = Counter(d["timestamp"][:10] for d in docs)
assert max(per_zone.values()) < 3000, f"polygon at response cap: {per_zone.most_common(1)}"

for d in docs:
    assert d["timestamp"] and d["zone"], f"missing core field: {d}"
    assert d["city"] == d["zone"].split(" - ")[0].strip(), f"bad cluster: {d['zone']}"
assert len({d["city"] for d in docs}) < len(per_zone), "no polygons were clustered"

by_city = {}
for d in docs:
    if d["region_en"] is not None:
        by_city.setdefault(d["city"], set()).add((d["region_en"], d["shelter_time_seconds"]))
mixed = [c for c, v in by_city.items() if len(v) > 1]
assert not mixed, f"city clusters with inconsistent region/shelter: {mixed[:3]}"

types = Counter(d["message_type"] for d in docs)
assert set(types) <= {"alert", "all_clear", "pre_alert", "info"}, set(types)
for d in docs:
    if "האירוע הסתיים" in (d["category_desc"] or ""):  # "the event has ended"
        assert d["message_type"] == "all_clear", f"all-clear filed as {d['message_type']}"

db = MongoClient("mongodb://localhost:27017", serverSelectionTimeoutMS=5000).alerts_db
assert db.alerts.count_documents({}) == len(docs), "database count differs from file"
assert db.meta.find_one({"_id": "load"}), "no completed load recorded"
for idx in ("by_region_en_time", "by_type_time", "by_zone_time", "by_municipality_time"):
    assert idx in db.alerts.index_information(), f"index {idx} missing"
assert "by_zone_sub" in db.users.index_information(), "users collection not set up"

try:
    db.alerts.insert_one({"_id": "__probe__", "timestamp": "2024-01-01T00:00:00"})
    db.alerts.delete_one({"_id": "__probe__"})
    raise AssertionError("validator accepted an alert with no zone")
except WriteError:
    pass

print(f"OK — {len(docs):,} docs ({types['alert']:,} alerts), {joined / len(docs):.1%} joined, "
      f"busiest polygon {max(per_zone.values()):,}, busiest day {max(per_day.values()):,}")
