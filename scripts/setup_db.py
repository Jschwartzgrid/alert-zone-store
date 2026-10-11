"""Create the collections, validators and indexes. Safe to run repeatedly."""

import os

from pymongo import ASCENDING as ASC, MongoClient

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = os.environ.get("MONGO_DB", "alerts_db")

# Pin only the fields every alert must have; new categories may add others.
ALERT_SCHEMA = {"$jsonSchema": {
    "bsonType": "object",
    "required": ["zone", "timestamp", "category", "message_type"],
    "properties": {
        "zone": {"bsonType": "string"},
        "timestamp": {"bsonType": "string"},
        "category": {"bsonType": "int"},
        "message_type": {"enum": ["alert", "all_clear", "pre_alert", "info"]},
    },
}}

USER_SCHEMA = {"$jsonSchema": {
    "bsonType": "object",
    "required": ["subscribed_zones", "last_delivered_ts"],
    "properties": {
        "subscribed_zones": {"bsonType": "array", "items": {"bsonType": "string"}},
        "last_delivered_ts": {"bsonType": "string"},
    },
}}

ALERT_INDEXES = {
    "by_type_time": [("message_type", ASC), ("timestamp", ASC)],
    "by_zone_time": [("zone", ASC), ("timestamp", ASC)],
    "by_city": [("city", ASC)],
    "by_municipality_time": [("municipality_en", ASC), ("timestamp", ASC)],
    "by_region_en_time": [("region_en", ASC), ("timestamp", ASC)],
}

USER_INDEXES = {
    "by_zone_sub": [("subscribed_zones", ASC)],
}


def ensure_collection(db, name, schema):
    if name in db.list_collection_names():
        db.command("collMod", name, validator=schema)
    else:
        db.create_collection(name, validator=schema)


def setup(db):
    ensure_collection(db, "alerts", ALERT_SCHEMA)
    ensure_collection(db, "users", USER_SCHEMA)
    for name, keys in ALERT_INDEXES.items():
        db.alerts.create_index(keys, name=name)
    for name, keys in USER_INDEXES.items():
        db.users.create_index(keys, name=name)


if __name__ == "__main__":
    db = MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)[DB_NAME]
    setup(db)
    print(f"{DB_NAME}: alerts and users collections ready, "
          f"{len(ALERT_INDEXES) + len(USER_INDEXES)} indexes")
