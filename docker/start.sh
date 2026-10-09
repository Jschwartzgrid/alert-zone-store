#!/bin/bash
set -euo pipefail

docker-entrypoint.sh mongod --bind_ip_all &
mongod_pid=$!

stop() { kill -TERM "$mongod_pid" 2>/dev/null || true; wait "$mongod_pid" || true; }
trap 'stop; exit 143' TERM INT
trap stop EXIT

until mongosh --quiet --eval 'db.adminCommand("ping")' >/dev/null 2>&1; do sleep 1; done

python /app/scripts/setup_db.py
python /app/scripts/load_to_mongo.py
echo "Ready: alerts_db is loaded"

wait "$mongod_pid"
