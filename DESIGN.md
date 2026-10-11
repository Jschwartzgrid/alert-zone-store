# Design

## Why MongoDB

Alert records do not share one shape. The feed carries 18 message types, from
rocket fire to earthquake notices and all-clears, and types have been added
over the period covered. A relational schema would need either a wide table of
mostly empty columns or a migration for each new type. A document store keeps
each record's own fields, and the `alerts` validator pins only the four every
record must have.

The queries never join. Region, shelter time, local authority and coordinates
are joined once, when the data is normalised, and stored on each alert, so every
read is a single-collection query or aggregation.

MongoDB also has an official Docker image, a mature Python driver and native
sharding. Every resident-facing query names its polygons, so `zone` (hashed) or
`{zone, timestamp}` would route those reads to the shards that hold them;
regional aggregations would fan out across shards, which is acceptable for
analysis.

## Alternatives

**PostgreSQL with JSONB** could hold the variable fields, but the planned
queries need no joins, so the relational model's main advantage goes unused
while the variable part of each record still needs JSONB columns and their own
indexes.

**Cassandra** is built for continuous, write-heavy workloads at very large
scale, with a table designed around each query. This store is loaded in
batches and read in several different ways, so that design and its operational
cost buy nothing here.

## Risks

| Risk | Handling |
|---|---|
| The source API changes or disappears | The system depends only on committed files. The history API has already moved host once |
| The API silently truncates large responses | One request per polygon keeps each response far under the 3,000-row cap; a polygon that reaches it stops the pull before anything is written |
| Retired polygon names are invisible to per-polygon queries | Whole-day responses fill them in, from days that came back complete |
| No unique record id in the source | `_id` is `rid:zone`; the tests assert it is unique |
| Region and shelter time change upstream | Reference snapshots are saved with the pull and reused, so normalisation is repeatable |
| A load is interrupted | Upserts on a deterministic key; the container reruns setup and load on start and restarts on failure |
| The dataset outgrows memory | The loader streams JSON Lines at about 30 MiB |
| New alert types with new fields | The validator requires four fields and accepts any others |
| Reference coverage degrades | `test_dataset.py` fails if under 99% of rows join |
| Scope drifts into live delivery | Live ingest and push to devices are outside this store |
