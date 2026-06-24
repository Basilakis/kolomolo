# Deployment

How to stand the system up — the **local PoC** path (what graders run) and the **scaled**
path (how it would run in production). Architecture rationale lives in
[`POC_ARCHITECTURE.md`](./POC_ARCHITECTURE.md) and [`SCALE_ARCHITECTURE.md`](./SCALE_ARCHITECTURE.md);
model/service choices in [`MODELS_AND_SERVICES.md`](./MODELS_AND_SERVICES.md).

---

## 1. Deployment topology at a glance

| Component | PoC (local) | Scaled (prod) |
|---|---|---|
| Graph DB | 1 Neo4j container | Neo4j cluster: 1 writer + N read replicas |
| Ingestion | `cli ingest` (one process, thread pool) | Celery workers pulling from a broker |
| Broker | none | RabbitMQ / SQS (Redis in the compose preview) |
| Serving app | Streamlit on host or `app` container | Streamlit/API behind a load balancer, N replicas |
| Object storage | local `data/` | S3 (PDFs); messages carry references |
| LLM | Anthropic API (hosted) | Anthropic API (or self-hosted model server) |
| Embeddings (baseline) | local sentence-transformers | local on each replica / shared cache |
| Observability | console logs | Sentry + New Relic + OpenTelemetry |

**One image, two roles.** The same [`Dockerfile`](../Dockerfile) builds both the serving app
and the ingestion workers — only the `command` differs. This keeps build and release trivial.

---

## 2. Local PoC deployment

### 2.1 Prerequisites
- Docker Desktop (for Neo4j)
- Python 3.10+
- An `ANTHROPIC_API_KEY`

### 2.2 Steps (raw commands; `make` equivalents in parentheses)

```bash
# 1. Install (make setup)
python -m venv .venv
.\.venv\Scripts\Activate.ps1            # Windows PowerShell  (Linux/mac: source .venv/bin/activate)
pip install -e .
copy .env.example .env                  # then set ANTHROPIC_API_KEY

# 2. Start the graph backend (make up)
docker compose up -d neo4j
#    Neo4j browser: http://localhost:7474  (neo4j / sleepmedcorp)

# 3. Put the data package in ./data/  (cpap-datasheets-and-manuals.zip)

# 4. Inventory the corpus first — no LLM, fast  (make inventory)
python -m cpap_graphrag.ingestion.cli inventory

# 5. Ingest  (make ingest WORKERS=8)
python -m cpap_graphrag.ingestion.cli ingest --workers 8
#    Re-running is cheap: unchanged files are skipped (content-hash dedup). --force to override.

# 6. Build the baseline index + evaluate  (make index-baseline && make eval)
python -c "from cpap_graphrag.baseline.vector_rag import build_index; print(build_index())"
python -m cpap_graphrag.eval.run_eval

# 7. Launch the app  (make app)
streamlit run app/streamlit_app.py      # http://localhost:8501
```

### 2.3 Fully containerized (optional)
```bash
docker compose --profile app up -d --build     # Neo4j + app  (make app-docker)
```
Inside the compose network the app reaches Neo4j at `bolt://neo4j:7687` (set automatically).

### 2.4 Smoke test the pipeline before a full run
```bash
python -m cpap_graphrag.ingestion.cli ingest --limit 1 --workers 2
```
Processes a single PDF end-to-end — confirms parsing, the API key, extraction, and Neo4j load
all work before you spend tokens on the whole corpus.

---

## 3. Release / promotion flow

```
git push ─▶ CI: lint + unit tests + `ingest --limit 1` smoke (mocked LLM)
        ─▶ build image, tag with git SHA, push to registry
        ─▶ deploy to staging ─▶ run eval harness, assert value/unit-correctness >= threshold
        ─▶ manual approve ─▶ deploy to prod (rolling), Sentry release tagged with the SHA
```

- **Eval is a release gate.** The [`eval/run_eval.py`](../src/cpap_graphrag/eval/run_eval.py)
  value/unit-correctness number must not regress — that's the core business metric, so it
  blocks promotion just like a failing test.
- **Sentry release tagging** ties any post-deploy error spike to the exact SHA for fast
  rollback (see [`SCALE_ARCHITECTURE.md §5`](./SCALE_ARCHITECTURE.md)).
- **Schema/graph migrations:** ingestion is additive and idempotent (`MERGE`); a breaking
  ontology change is deployed by re-ingesting into a new graph version and flipping the
  serving plane's read target (blue/green on the data, not just the code).

---

## 4. Scaled deployment (production)

Bring up the illustrative broker + worker locally:
```bash
docker compose --profile scale up -d --build      # Neo4j + Redis + worker  (make scale)
```

In a real environment this maps to:
- **Workers** — autoscaled on **queue depth / message age** (New Relic metric → autoscaler).
  Separate worker pools per stage so the expensive `extract` pool scales independently of
  cheap `parse`/`load`.
- **Broker** — managed RabbitMQ or SQS with a **dead-letter queue** for poison documents.
- **Serving** — stateless Streamlit/API replicas behind a load balancer; a Redis cache for
  hot spec-lookups/comparisons keyed by the ingest watermark.
- **Neo4j** — causal cluster, read replicas serve the (read-only) query path.
- **Secrets** — `ANTHROPIC_API_KEY`, DB creds via the platform secret manager, never baked
  into the image.

Config is entirely environment-driven ([`config.py`](../src/cpap_graphrag/config.py) reads
`.env`/env vars), so the same image runs unchanged across local, staging, and prod.

---

## 5. Operational runbook (essentials)

| Situation | Action |
|---|---|
| Re-ingest everything after a prompt change | `ingest --force` (bypasses content-hash skip) |
| One document keeps failing | At scale it lands in the **DLQ** + a Sentry issue; inspect payload, fix, replay |
| Serving latency breaches SLO | Check New Relic transaction trace: planner LLM vs Cypher vs answer LLM span |
| LLM cost spike | New Relic tokens/cost metric; usually a bulk re-ingest — gate behind the hash guard |
| Wipe the graph (dev only) | `make clean` (removes volumes — destroys data) |
