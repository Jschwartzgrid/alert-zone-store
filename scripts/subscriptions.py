"""Per-user delivery state, so an alert is never sent to the same person twice."""

import os

from pymongo import ASCENDING, MongoClient

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = os.environ.get("MONGO_DB", "alerts_db")
EPOCH = "1970-01-01T00:00:00"


def zones_for_city(db, city):
    return sorted(db.alerts.distinct("zone", {"city": city}))


def subscribe(db, user_id, zones, start_from=EPOCH):
    db.users.replace_one(
        {"_id": user_id},
        {"_id": user_id, "subscribed_zones": list(zones), "last_delivered_ts": start_from},
        upsert=True,
    )


def pending(db, user_id, limit=None):
    user = db.users.find_one({"_id": user_id})
    if user is None:
        raise KeyError(f"no such user: {user_id}")
    cur = db.alerts.find({
        "message_type": "alert",
        "zone": {"$in": user["subscribed_zones"]},
        "timestamp": {"$gt": user["last_delivered_ts"]},
    }).sort("timestamp", ASCENDING)
    return list(cur.limit(limit) if limit else cur)


def deliver(db, user_id, limit=None):
    batch = pending(db, user_id, limit)
    if not batch:
        return []
    # Advance only after handing the batch over: a crash re-sends, never drops.
    db.users.update_one({"_id": user_id}, {"$set": {"last_delivered_ts": batch[-1]["timestamp"]}})
    return batch


def demo():
    db = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)[DB_NAME]

    haifa = zones_for_city(db, "חיפה")
    print(f"Haifa is {len(haifa)} polygons")

    subscribe(db, "user:carmel", [z for z in haifa if "כרמל" in z])
    subscribe(db, "user:all-haifa", haifa)

    narrow = pending(db, "user:carmel")
    broad = pending(db, "user:all-haifa")
    print(f"pending for a single-polygon resident: {len(narrow)}")
    print(f"pending for a whole-city subscriber:    {len(broad)}")
    print(f"-> subscribing by city would over-alert this resident {len(broad) - len(narrow)} times")

    first = deliver(db, "user:carmel", limit=5)
    assert len(pending(db, "user:carmel")) == len(narrow) - len(first)
    second = deliver(db, "user:carmel", limit=5)
    assert not ({d["_id"] for d in first} & {d["_id"] for d in second}), "alert retransmitted"
    print(f"delivered {len(first)} + {len(second)}, no alert sent twice")


if __name__ == "__main__":
    demo()
