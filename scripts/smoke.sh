#!/usr/bin/env bash
# Local end-to-end smoke: Neo4j up -> ingest ONE document -> one query per category.
# Requires Docker, the corpus zip in data/, and ANTHROPIC_API_KEY in .env.
#
# Usage:  bash scripts/smoke.sh
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> Starting Neo4j"
docker compose up -d neo4j

echo "==> Waiting for Neo4j to be healthy"
for _ in $(seq 1 30); do
  status=$(docker inspect -f '{{.State.Health.Status}}' cpap-neo4j 2>/dev/null || echo starting)
  [ "$status" = "healthy" ] && break
  sleep 2
done
echo "    Neo4j: ${status:-unknown}"

echo "==> Ingesting one document (smoke scope)"
python -m cpap_graphrag.ingestion.cli ingest --limit 1 --workers 4

echo "==> Running per-category smoke queries"
python scripts/smoke.py