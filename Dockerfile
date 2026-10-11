FROM mongo:8

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-venv \
 && rm -rf /var/lib/apt/lists/* \
 && python3 -m venv /opt/venv

ENV PATH="/opt/venv/bin:$PATH" \
    MONGO_URI="mongodb://127.0.0.1:27017" \
    ALERTS_FILE="/app/data/alerts_clean.jsonl.gz"

COPY requirements.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY scripts/ /app/scripts/
COPY data/alerts_clean.jsonl.gz /app/data/
COPY docker/start.sh /app/start.sh
RUN chmod +x /app/start.sh

HEALTHCHECK --interval=5s --timeout=5s --start-period=10s --retries=120 \
  CMD mongosh --quiet --eval 'quit(db.getSiblingDB("alerts_db").meta.findOne({_id: "load"}) ? 0 : 1)'

ENTRYPOINT ["/app/start.sh"]
CMD []
