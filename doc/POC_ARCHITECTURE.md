# PoC Architecture — CPAP Knowledge Graph & GraphRAG

> How the proof-of-concept works **today**: a local, single-machine system that ingests a
> fixed corpus of CPAP datasheets/manuals into a knowledge graph and answers comparison &
> recommendation questions with audit-grade citations. For the production/at-scale design,
> see [`SCALE_ARCHITECTURE.md`](./SCALE_ARCHITECTURE.md).

---

## 1. Scope & guiding principle

| Property | PoC reality |
|---|---|
| Corpus | Fixed zip of "dozens" of vendor PDFs, known ahead of time |
| Execution | Local, single process, single machine |
| Ingestion trigger | Manual — you run `cli ingest` |
| Concurrency need | Only the LLM extraction stage (bounded thread pool) |
| Infrastructure | One Neo4j container + Python; **no broker, no workers, no message queue** |
| Latency target | Serving: "seconds, not minutes". Ingestion: minutes, run once |

**Guiding principle:** *smallest viable architecture*. We add infrastructure only when a
metric or requirement forces it. A queue, workers, and a telemetry stack are the right
answer at scale (documented separately) — introducing them for a fixed corpus would be
unjustified complexity we'd have to defend with no benefit to point at.

## 2. The two planes

The system splits into an **offline ingestion plane** and an **online serving plane**.
They share nothing at runtime except the Neo4j database. This is the single most important
structural decision: cost and latency live in different places, so each plane is tuned
independently.

```
┌───────────────────────────  INGESTION PLANE (offline, batch)  ───────────────────────────┐
│                                                                                          │
│  zip ─▶ inventory ─▶ [per file] hash-check ─▶ parse ─▶ segment ─▶ extract ─▶ normalize    │
│        (no LLM)        (skip if unchanged)   (PyMuPDF/  (table-   (Claude,    (UCUM/QUDT   │
│                                              pdfplumber) aware)   pooled,     + pint)      │
│                                                                   schema-                  │
│                                                                   forced)                  │
│                                                            ─▶ resolve ─▶ load ─▶ mark      │
│                                                              (dedup     (Neo4j   ingested  │
│                                                               vendor    MERGE)   (:SourceFile)
│                                                               names)                       │
└──────────────────────────────────────────────────────────────────────────────────────────┘
                                              │
                                       ┌──────▼──────┐
                                       │   Neo4j     │  (:Device)-[:HAS_PARAMETER]->(:Quantity)
                                       │  knowledge  │  (:Quantity)-[:SOURCED_FROM {page}]->(:Document)
                                       │   graph     │  (:Device)-[:HAS_FEATURE]->(:Feature)
                                       └──────▲──────┘  (:Device)-[:SUPPORTS_MODE]->(:Mode)
                                              │
┌────────────────────────────  SERVING PLANE (online, per-query)  ───────────────────────────┐
│                                                                                            │
│  question ─▶ planner ─▶ route ─▶ Cypher template ─▶ rows ─▶ grounded answer ─▶ Streamlit    │
│             (Claude,   (determ-   (graph/queries)   (+prov)  (Claude, answer-   (answer +    │
│              classify   inistic,                            only-from-rows)     subgraph +   │
│              + slots)   per type)                          refuse if no rows    citations)   │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

Code map: ingestion = [`src/cpap_graphrag/ingestion/`](../src/cpap_graphrag/ingestion/) driven by
[`cli.py`](../src/cpap_graphrag/ingestion/cli.py); serving = [`agent/`](../src/cpap_graphrag/agent/)
ending in [`answer.py`](../src/cpap_graphrag/agent/answer.py); graph contract =
[`ontology/schema.py`](../src/cpap_graphrag/ontology/schema.py).

## 3. Ingestion pipeline (stage by stage)

| Stage | Module | What it does | Cost |
|---|---|---|---|
| **inventory** | `inventory.py` | Unzip; per-file vendor/model/doc-type/pages manifest. Run first. | cheap |
| **hash-check** | `state.py` | sha256 the file; skip if already ingested (unchanged). | cheap |
| **parse** | `parse.py` | PyMuPDF text + pdfplumber tables, **per-page provenance**. | cheap |
| **segment** | `segment.py` | Table-aware chunking (spec tables kept intact). | cheap |
| **extract** | `extract.py` | Claude, **schema-forced** → `{value/min/max/default, unit, page}`. | **slow / $$$** |
| **normalize** | `ontology/units.py` | Map unit → UCUM/QUDT + SI-canonical magnitude. | cheap |
| **resolve** | `resolve.py` | Collapse vendor naming variants → one canonical `Device`. | cheap |
| **load** | `load.py` | `MERGE` into Neo4j with provenance edges. | cheap |
| **mark** | `state.py` | Record `(:SourceFile {content_hash})` on success. | cheap |

The whole cost/latency budget of ingestion is concentrated in **one stage: extraction**.
Everything else is local CPU/IO. That observation drives the only concurrency in the PoC.

## 4. Concurrency: a bounded thread pool, not a queue

Extraction issues one Claude call per segment, and segments are independent. So
`extract_segments()` fans out across a **bounded `ThreadPoolExecutor`**
([`extract.py`](../src/cpap_graphrag/ingestion/extract.py)):

```python
with ThreadPoolExecutor(max_workers=workers) as pool:   # default 8
    for rec in pool.map(extract_segment, segs):
        ...
```

- **Why bounded?** The cap protects the Anthropic rate limit and bounds memory/cost. It is
  the single knob that turns a ~20-minute sequential ingest into a few minutes.
- **Why a thread pool, not async?** The work is I/O-bound (waiting on HTTP); threads are
  simplest and the SDK is sync. No event-loop ceremony needed at this scale.
- **Why not a message queue?** A queue earns its keep when work arrives continuously, when
  producers/consumers are separate processes, or when you need durable cross-process retry.
  None hold here: it's a one-shot batch over a fixed zip in a single process. See
  [`SCALE_ARCHITECTURE.md §2`](./SCALE_ARCHITECTURE.md) for the exact conditions that flip
  this decision.
- **Failure isolation:** `extract_segment` catches per-segment errors and returns `None`, so
  one bad segment (or a transient 429) can't abort the batch.

## 5. Idempotency & crash-safety (what replaces a durable queue)

Two mechanisms give us queue-like reliability without a broker:

1. **Content-hash dedup** ([`state.py`](../src/cpap_graphrag/ingestion/state.py)). Each file
   is sha256'd. A `(:SourceFile {content_hash, ingested_at})` marker is written **only after a
   successful load**. On the next run, unchanged files are skipped — no duplicate LLM cost.
   An *edited* datasheet has new bytes → new hash → it re-ingests automatically. `--force`
   overrides.
2. **`MERGE`-based load** ([`load.py`](../src/cpap_graphrag/ingestion/load.py)). Devices,
   vendors, modes and documents are `MERGE`d on stable keys, so loading the same record twice
   converges instead of duplicating.

Together these mean: **if ingestion crashes at file 30 of 40, you just re-run it.** Files
1–29 are skipped by hash; 30 onward are processed. That is crash recovery by construction —
the property a durable queue would otherwise provide.

## 6. Serving plane (per query)

1. **Plan** — [`planner.py`](../src/cpap_graphrag/agent/planner.py): Claude classifies the
   question into one of the seven target query types and fills slots (devices, parameters,
   feature, mode, numeric constraints) via a forced schema. *Routing is then deterministic.*
2. **Route + execute** — [`tools.py`](../src/cpap_graphrag/agent/tools.py) maps each query
   type to a fixed Cypher template in [`queries.py`](../src/cpap_graphrag/graph/queries.py).
   Templates (not free-form text-to-Cypher) make queries auditable and hard to hallucinate.
   Numeric constraints (#6) and recommendation ranking (#7) run on **canonical SI magnitudes**
   so cross-vendor comparison is valid regardless of the unit a datasheet printed.
3. **Ground + answer** — [`answer.py`](../src/cpap_graphrag/agent/answer.py): Claude answers
   from **only** the returned rows (each carrying doc+page), cites every fact, and **refuses**
   if there is no supporting subgraph. This is the core hallucination guardrail.
4. **Present** — [`app/streamlit_app.py`](../app/streamlit_app.py) shows answer + supporting
   subgraph + citations, with an optional vector-baseline column for live comparison.

## 7. Why this is the right PoC architecture (the defensible summary)

- **Separation of planes** lets serving stay fast (graph-query bound) while ingestion can be
  slow (LLM bound) — they never block each other.
- **Idempotent MERGE + content-hash** give crash-safe, cheap re-runs without a broker.
- **Bounded thread pool** addresses the *only* real concurrency need (extraction) with the
  simplest tool that fits.
- **Templated query routing** is the concrete evidence of "optimised for the seven query
  types" and underpins the hallucination story.

Every piece of heavier infrastructure (brokers, worker fleets, autoscaling, an observability
stack) is deliberately **out of scope here and documented in the scale design** — so we can
show we know exactly *when* and *why* each would be added, without paying for it prematurely.
