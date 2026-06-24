# Scale Architecture — Running CPAP GraphRAG in Production

> How the system evolves from the local proof-of-concept
> ([`POC_ARCHITECTURE.md`](./POC_ARCHITECTURE.md)) into a continuously-operating,
> multi-source, observable production service. This document defines the moving parts —
> **queue management, telemetry, Sentry, and New Relic** — so the design choices are
> explicit and defensible, not hand-waved.

The PoC and this design share the **same graph contract and the same query path**. The
offline/online split means everything below can be added *behind* that contract without
touching the serving logic.

---

## 1. When the PoC stops being enough (the triggers)

A queue + worker fleet + observability stack is the right answer when one or more of these
become true. Each is a concrete, named trigger — not "it might get bigger":

| Trigger | PoC assumption that breaks |
|---|---|
| **Continuous ingestion** — vendors publish new/updated datasheets weekly | Fixed zip, processed once |
| **Multiple producers** — clinicians/integrations upload concurrently | Single CLI invocation |
| **Volume** — thousands of documents, not dozens | Fits in one batch run |
| **Cross-process durability** — a crashed worker must not lose work | Single process; re-run the CLI |
| **Independent scaling** — extraction throughput must scale separately from the API | One machine |
| **SLOs & on-call** — "answers in < 3s p95, ingestion freshness < 1h" must be *measured* | Eyeballed locally |

When these hold, in-process threads no longer suffice and we move extraction onto a
**distributed work queue**, and we instrument everything with **telemetry**.

## 2. Target architecture

```
        producers                         BROKER (queue)                 worker fleet
  ┌──────────────────┐            ┌──────────────────────────┐      ┌───────────────────┐
  │ upload API /     │  enqueue   │  ingest.parse            │ pull │  parse workers     │
  │ vendor webhook / ├───────────▶│  ingest.extract  (jobs)  │─────▶│  extract workers   │──┐
  │ scheduled crawler│            │  ingest.load             │      │  load workers      │  │
  └──────────────────┘            │  + retries + DLQ         │      └───────────────────┘  │
                                  └──────────────────────────┘                ▲            │
                                        ▲      │ dead-letter                   │ autoscale  │
                                        │      ▼                          (on queue depth)  │
                                        │  ┌─────────┐                                      ▼
                                        │  │   DLQ   │                                ┌──────────┐
                                        │  └─────────┘                                │  Neo4j   │
                                        │                                             │  cluster │
   ┌────────────────────────────────────┴───────── OBSERVABILITY ──────────┐         │ (1 write,│
   │  New Relic (APM, infra, queue metrics)   Sentry (errors, perf traces)  │         │ N read)  │
   │  OpenTelemetry traces · Prometheus/StatsD metrics · structured logs    │         └────▲─────┘
   └───────────────────────────────────────────────────────────────────────┘              │
                                                                                           │
  user ─▶ load balancer ─▶ serving API (planner→Cypher→answer) ─▶ cache ───────────────────┘
                              (horizontally scaled, stateless)   (Redis: hot specs/comparisons)
```

---

## 3. Queue management

### 3.1 Definitions (the vocabulary)

- **Message queue / broker** — middleware that holds *messages* (units of work) between
  producers and consumers so they don't have to run at the same time or speed. Examples:
  RabbitMQ, Amazon SQS, Redis (via Celery/RQ), Kafka. It decouples *who creates work* from
  *who does it*.
- **Producer** — code that *enqueues* a job (e.g. the upload API publishing
  `ingest.parse(document_id=…)`).
- **Consumer / worker** — a long-running process that *pulls* jobs and executes them (e.g. an
  extraction worker calling Claude).
- **Job / task** — one unit of work + its payload. Ours are small and reference-based:
  `{"document_id": "...", "content_hash": "..."}`, **not** the PDF bytes (keep messages tiny;
  put blobs in object storage).
- **Concurrency / prefetch** — how many jobs a worker processes at once, and how many it
  reserves ("prefetches") from the broker. This is the distributed equivalent of the PoC's
  `max_workers` thread cap, and it's how we respect the **LLM rate limit** across the fleet.
- **Acknowledgement (ack/nack)** — a worker *acks* a message only after the job succeeds, so
  if it crashes mid-job the broker re-delivers the message to another worker. *Ack-on-success,
  not ack-on-receipt* is what makes the queue durable.
- **Delivery semantics** — **at-least-once** (default; a job may run more than once, so jobs
  must be **idempotent**) vs **exactly-once** (expensive, rarely truly available). We design
  for at-least-once + idempotent jobs.
- **Idempotency** — running a job twice yields the same end state. We already have this: the
  `content_hash` dedup guard ([`state.py`](../src/cpap_graphrag/ingestion/state.py)) and the
  `MERGE`-based load ([`load.py`](../src/cpap_graphrag/ingestion/load.py)) carry straight over.
- **Retry with backoff** — on transient failure (e.g. a `429` from the LLM), the job is
  retried after an increasing, jittered delay (e.g. 2s, 8s, 30s) instead of hammering the API.
- **Dead-letter queue (DLQ)** — a side queue where a message lands after it exhausts its
  retries (a "poison message"). The DLQ stops one bad document from blocking the pipeline and
  gives humans a place to inspect/replay failures.
- **Backpressure** — when consumers fall behind, the queue depth grows; producers (or
  autoscalers) react instead of overwhelming downstream. The queue *absorbs* bursts.
- **Priority / routing** — separate queues per stage (`parse`, `extract`, `load`) and per
  priority (e.g. an interactive re-ingest vs a nightly bulk crawl) so latency-sensitive work
  isn't stuck behind a backlog.

### 3.2 Recommended stack

- **Broker:** RabbitMQ or Amazon SQS. (SQS = zero-ops managed; RabbitMQ = richer routing.)
- **Worker framework:** **Celery** (Python-native, first-class retries/DLQ, mature Sentry &
  New Relic integrations).
- **Queue topology:** one queue per pipeline stage so each scales independently —
  `parse` (cheap, IO-bound) and `extract` (expensive, LLM-bound) have very different worker
  counts and rate limits.

### 3.3 Ingestion at scale — the flow

```
webhook/upload ─▶ store PDF in object storage (S3) ─▶ enqueue ingest.parse{document_id}
ingest.parse   ─▶ produces segments ─▶ enqueue ingest.extract{segment_id} (fan-out)
ingest.extract ─▶ Claude (rate-limited, retried) ─▶ normalize ─▶ enqueue ingest.load{record}
ingest.load    ─▶ resolve + MERGE into Neo4j ─▶ mark (:SourceFile{content_hash}) ─▶ ack
   on repeated failure at any stage ─▶ DLQ ─▶ alert + manual replay
```

The PoC's linear `parse→segment→extract→resolve→load` becomes the **same stages as queue
hops**. The logic in each stage module is reused; only the *orchestration* changes from an
in-process loop to enqueue/consume.

---

## 4. Observability / telemetry

### 4.1 Definitions

- **Telemetry** — the signals a running system emits about itself. The **three pillars**:
  - **Metrics** — cheap numeric time-series (counts, rates, gauges, histograms): queue depth,
    jobs/sec, extraction latency p95, LLM cost/hour, answer-refusal rate.
  - **Logs** — timestamped event records. We use **structured logs** (JSON with
    `document_id`, `job_id`, `trace_id`) so they're queryable, not prose.
  - **Traces** — the end-to-end path of one request/job across services, broken into **spans**
    (parse span → extract span → load span), tying the whole journey to one `trace_id`. We use
    **OpenTelemetry** as the vendor-neutral standard, exporting to New Relic.
- **SLI / SLO / SLA**:
  - **SLI** (indicator) — a measured number, e.g. "p95 answer latency".
  - **SLO** (objective) — the target, e.g. "p95 answer latency < 3s, 99% of the time".
  - **SLA** (agreement) — the contractual promise + consequences. SLOs drive alerting.
- **RED method** (for request-driven services like the serving API): **R**ate, **E**rrors,
  **D**uration. **USE method** (for resources like workers/DB): **U**tilisation, **S**aturation,
  **E**rrors.
- **Telemetry review** — the *operational practice* of regularly reading these signals: a
  recurring (e.g. weekly) review of dashboards and alerts where the team checks SLO burn,
  error-rate trends, queue health, LLM cost drift, and extraction-quality metrics (value/unit
  correctness from the eval harness run against production samples). Its output is action:
  tune worker counts, fix top Sentry issues, adjust alert thresholds, re-prompt extraction.
  It's how observability becomes decisions instead of dashboards nobody reads.

### 4.2 What we instrument (per plane)

| Plane | Key metrics (examples) |
|---|---|
| **Ingestion** | queue depth per stage, **message age** (oldest unacked), jobs/sec, extract latency p50/p95, retry rate, DLQ count, LLM tokens & cost/hour, ingestion freshness (time from upload → queryable) |
| **Serving** | RED: requests/sec, error rate, latency p50/p95/p99; refusal rate (no-evidence answers); cache hit rate; per-query-type latency; citation-resolution rate |
| **Graph** | Neo4j query latency, active connections, page-cache hit ratio, read-replica lag |

---

## 5. Sentry (error & performance monitoring)

### 5.1 Definition

**Sentry** is an application-monitoring tool focused on **errors and performance**. When code
throws, Sentry captures the exception with full stack trace, local variables, the request/job
context, and a **breadcrumb** trail (the events leading up to it), then **groups** identical
errors into a single *issue* with frequency, first/last-seen, and affected releases. It also
does lightweight performance tracing and **release health** (which deploy introduced a
regression).

### 5.2 How we use it — for the web service *and* the queue

- **Serving API:** every unhandled exception (a malformed plan, a Neo4j timeout, an LLM error)
  becomes a Sentry issue tagged with `query_type`, `trace_id`, and release. We alert when a new
  issue appears or an error rate spikes.
- **Queue / workers (the important part):** Sentry's **Celery integration** automatically
  captures **failed tasks** — it reports the exception *with the job payload and stage* so a
  poison document is immediately diagnosable. Crucially, Sentry distinguishes a task that
  *eventually succeeded after retries* from one that **exhausted retries and hit the DLQ**, so
  DLQ growth shows up as a spiking Sentry issue, not just a silent number.
- **Context that makes failures actionable:** we tag every event with `document_id`,
  `content_hash`, `vendor`, and `trace_id`, so a Sentry issue links directly to the offending
  source file and its New Relic trace.
- **Release health:** if a new extraction-prompt deploy raises the worker error rate, Sentry
  attributes it to that release so we can roll back fast.

**One-line role:** *Sentry answers "what broke, where, in which release, and with what
payload" — for both the API and the background workers.*

## 6. New Relic (APM, infrastructure & queue monitoring)

### 6.1 Definition

**New Relic** is an **observability/APM (Application Performance Monitoring) platform**. Where
Sentry is error-centric, New Relic is **throughput-, latency-, and resource-centric**: it
continuously charts how the *whole system* performs, supports **distributed tracing** across
services, monitors **infrastructure** (CPU/memory/host health), and lets us build **dashboards
and alert policies** over any metric.

### 6.2 How we use it — including queues

- **APM on the serving API:** automatic RED metrics, transaction traces, and a breakdown of
  where each request spends time (planner LLM call vs Cypher vs answer LLM call) — this is how
  we defend the "seconds, not minutes" SLO with data and find the slow span.
- **Queue monitoring (the key bit):** New Relic tracks **queue health** as first-class metrics —
  **queue depth** (backlog size), **throughput** (enqueue vs process rate), **message/job age**
  (how long the oldest job has waited), **processing time per stage**, and **worker
  utilisation/saturation**. A persistently rising `extract` queue depth or job age is the
  signal that we're LLM-bound and must add extract workers (or that we're being rate-limited).
- **Autoscaling input:** those same queue-depth metrics drive **autoscaling** — worker count
  scales out when backlog/age crosses a threshold and back in when drained, so we pay for
  extraction capacity only when there's a backlog.
- **Infrastructure & DB:** host metrics for the worker fleet and Neo4j (CPU, memory, page-cache
  hit ratio, read-replica lag), so resource saturation is visible before it causes errors.
- **Distributed tracing:** OpenTelemetry `trace_id`s flow through New Relic so one upload can
  be followed upload → parse → extract → load → "queryable", measuring end-to-end **ingestion
  freshness**.
- **Alerting:** policies on SLO breaches (latency p95, error rate, DLQ size, queue age, cost
  budget) page on-call; these alerts are the agenda for the **telemetry review** (§4.1).

**One-line role:** *New Relic answers "is the system healthy and fast, where is the
bottleneck, and is the queue keeping up" — and feeds autoscaling and on-call.*

### 6.3 Sentry vs New Relic — why both

| | **Sentry** | **New Relic** |
|---|---|---|
| Primary lens | Errors & exceptions | Performance, throughput, resources |
| Best at | "What broke and why" (stack trace + payload) | "Is it healthy/fast; where's the bottleneck" |
| For queues | Failed/poison tasks, DLQ as issues, release health | Queue depth, age, throughput, autoscale signal |
| Granularity | Per-exception, grouped into issues | Per-transaction/metric, dashboards & traces |
| On-call use | Triage the specific failure | Detect SLO breach & saturation |

They overlap a little (both do some tracing) but answer different questions; production runs
both, correlated by a shared `trace_id`.

---

## 7. Reliability patterns (summary)

- **At-least-once + idempotent jobs** → safe re-delivery (content-hash + `MERGE`).
- **Retry with exponential backoff + jitter** → ride out transient LLM/DB errors without
  thundering-herd.
- **DLQ + alert + replay** → isolate poison documents; never block the pipeline; humans
  inspect and re-enqueue after a fix.
- **Per-stage queues + prefetch limits** → respect the LLM rate limit globally and let cheap
  stages outpace expensive ones.
- **Graceful degradation** → if the LLM answer-synthesis is down, the serving API can still
  return the structured subgraph + citations (the facts), just without prose.

## 8. Scaling the data stores

- **Neo4j:** single writer + **read replicas** for the serving plane (queries are read-only);
  causal-cluster for HA. Indexes already defined in
  [`schema.py`](../src/cpap_graphrag/ontology/schema.py) cover the hot query types.
- **Cache (Redis):** memoize hot, deterministic results (popular spec lookups, common
  comparisons). Cache key includes the graph **version/ingest watermark** so an ingest
  invalidates stale answers.
- **Object storage (S3):** source PDFs live here; queue messages carry references, not bytes.

## 9. Cost model at scale

- **Ingestion cost** is dominated by extraction LLM tokens and is **one-off per document
  version** (content-hash dedup guarantees we never re-extract unchanged files). Bulk
  re-ingests are the main cost lever — gate them behind the hash guard and batch them off-peak.
- **Serving cost** = planner + answer LLM calls per query, cut by the Redis cache on hot
  queries. Per-query cost and token usage are tracked as New Relic metrics and reviewed for
  drift in the telemetry review.

---

## 10. The honest position (for the presentation)

> *"The PoC uses a bounded in-process thread pool and idempotent `MERGE` loading — correct for
> a fixed corpus, with no broker to justify. The moment ingestion becomes continuous and
> multi-source, the same stages become jobs on a Celery + RabbitMQ/SQS queue with retries, a
> DLQ, and rate-limit-aware prefetch; the offline/online split means the serving path is
> untouched. We make it observable with the three telemetry pillars over OpenTelemetry —
> **Sentry** for errors and failed/poison tasks (including DLQ and release health), **New
> Relic** for APM, queue depth/age/throughput, autoscaling signals and SLO alerting — and we
> close the loop with a recurring telemetry review that turns those signals into action.
> Idempotency (content-hash + `MERGE`) is the single property that makes at-least-once queue
> delivery safe, and it's already in the PoC."*
