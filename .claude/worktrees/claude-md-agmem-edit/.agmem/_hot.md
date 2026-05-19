<!-- agmem:hot generated=2026-05-09T20:56:05+00:00 project=agmem -->

# Project memory snapshot

## Constraints
- Never run terraform destroy in prod

## Facts
- agmem stores entries in JSONL with append-only semantics, ULID ids, and fcntl atomic write
