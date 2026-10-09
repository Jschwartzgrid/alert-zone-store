"""Stream the alert dataset into MongoDB. Idempotent: re-running changes nothing."""

import gzip
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from pymongo import MongoClient, ReplaceOne

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = os.environ.get("MONGO_DB", "alerts_db")
INPUT = Path(os.environ.get("ALERTS_FILE", "data/alerts_clean.jsonl.gz"))
BATCH = 1000


def iter_docs(path):
    # Line by line, so memory stays flat however large the file gets.
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load(db):
    read = written = 0
    batch = []

    def flush():
        nonlocal written
        res = db.alerts.bulk_write(batch, ordered=False)
        written += res.upserted_count + res.modified_count
        batch.clear()

    for d in iter_docs(INPUT):
        batch.append(ReplaceOne({"_id": d["_id"]}, d, upsert=True))
        read += 1
        if len(batch) == BATCH:
            flush()
            if read % 100_000 == 0:
                print(f"  {read:,} read", flush=True)
    if batch:
        flush()

    # Written last, so its presence means a load ran to completion.
    db.meta.replace_one({"_id": "load"}, {
        "_id": "load",
        "file": INPUT.name,
        "sha256": sha256(INPUT),
        "documents": read,
        "completed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }, upsert=True)
    return read, written


if __name__ == "__main__":
    if not INPUT.exists():
        sys.exit(f"{INPUT} not found")
    db = MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)[DB_NAME]
    print(f"Loading {INPUT}")
    read, written = load(db)
    print(f"Read {read:,} documents; {written:,} inserted or changed; "
          f"collection holds {db.alerts.estimated_document_count():,}")
