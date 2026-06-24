# Presentation content — SleepMedCorp CPAP GraphRAG

> Ready-to-paste slide content (the 6–7 slides the assignment mandates). Honest to the current
> build state. Paste each slide block into Lovable. Live demo: https://kolomolo.fly.dev/

---

## Slide 1 — Problem & business context

**Title:** CPAP selection is slow and error-prone — especially on the numbers

- SleepMedCorp clinicians must **compare** CPAP/BiPAP/APAP devices and **recommend** one matched
  to a patient's prescription and lifestyle.
- The knowledge is scattered across **dozens of vendor manuals & datasheets** — side-by-side
  comparison and patient-tailored recommendation are slow and mistake-prone, *especially on
  numeric parameters* (pressure ranges, weight, noise, ramp).
- Goal: an **architect-level GraphRAG PoC** that answers comparison & recommendation questions
  with **audit-grade citations** (source document + page).
- Optimised for **7 target query types**: spec lookup · parameter/unit/range/default · feature
  lookup · differentiator/comparison · mode & indication · multi-device numeric constraint ·
  patient-tailored recommendation.

---

## Slide 2 — Architecture decisions

**Title:** Two planes, one graph contract

- **Ingestion (offline):** PDF → page split → **Claude reads each page natively (vision+text)** →
  schema-forced extraction → **UCUM/QUDT unit normalization** → entity resolution → **Neo4j**.
- **Serving (online):** question → **planner** classifies into 1 of 7 types → **deterministic
  Cypher template** (not free-form text-to-SQL) → grounded answer + citations + subgraph → refuse
  if no evidence.
- **Why these choices:**
  - **Neo4j (Aura):** Cypher multi-hop joins are ideal for comparison/constraint; APOC for dedup.
  - **Custom thin agent, not LangGraph/CrewAI:** the problem is a *bounded* 7-type set we want to
    constrain — deterministic routing makes answers auditable (key for hallucination control).
  - **Claude Opus** for extraction (errors there are permanent); configurable cheaper tier for serving.
  - **Streamlit** frontend (answer + supporting subgraph + clickable source-page citations).
- **Units are first-class:** every measured parameter is a `(:Quantity)` node with
  `min/max/default`, UCUM code, and an **SI-canonical magnitude** so cross-vendor numeric compare works.

---

## Slide 3 — Data science decisions

**Title:** Turning messy vendor PDFs into a rigorous graph

- **Extraction:** schema-forced tool call — every numeric value carries `{value|min|max|default,
  unit, source_doc, page}`. *Numbers are never free text.* Native-PDF reading recovers values that
  naive text extraction loses (e.g. DreamStation pressure trapped in a layout-broken spec table).
- **Units (the core rigour):** map printed units → UCUM/QUDT (cmH₂O, kg, min, dB, …) + SI canonical;
  defensive decimal-comma handling (`1,33` → 1.33).
- **Multi-device + relevance gate:** a catalog page can list many devices; non-PAP products
  (masks, O₂ concentrators, nebulizers) and standards/guideline docs are dropped.
- **Entity resolution:** vendor-alias normalization (Philips/Respironics, BMC legal suffixes,
  GIII→G3) + **self-healing graph dedup** so duplicates can't accumulate across ingests.
  → reduced **60 noisy → 35 clean devices**.
- **Query planning:** LLM does only classify + slot-fill under a forced schema; routing to fixed
  Cypher per query type is deterministic and inspectable.

---

## Slide 4 — Metrics, observed results, baseline gap

**Title:** Measuring what matters — numeric correctness first

- **Chosen metrics (and why):**
  - **Value/Unit correctness (primary)** — the core business value; "units are not free text".
  - Citation validity (do doc+page resolve), answer faithfulness (grounded-in-citations).
  - Constraint-satisfaction accuracy (#6/#7); latency p50/p95; cost/query.
- **Observed so far:**
  - Graph: **35 devices · 188 quantities · 15 features · 23 modes** from 28 PDFs (23 contributed).
  - Live: *"pressure range of the DreamStation"* → **4–20 cm H₂O [DataSheet p.2]**, value/unit
    guard **passed**, routed as `spec_lookup`, ≈ **$0.038/query**, low-seconds latency.
- **GraphRAG vs vector-only baseline:** harness + baseline are built; **comparison run is the key
  remaining step.** Hypothesis: largest GraphRAG gain on #2 (unit/range/default), #6 (numeric
  constraints), #7 (ranking) — where vector similarity can't do numeric joins.
- *Honest note: numeric precision is partly delivered — a parameter-mapping fix + re-ingest is the
  next quality lever.*

---

## Slide 5 — Hallucination assurance (dedicated)

**Title:** Prevent · Detect · Mitigate — grounded by construction

- **Prevent:** schema-forced extraction (no free-text numbers); answers built **only** from
  returned graph nodes; **templated Cypher** (no hallucinated query logic).
- **Detect:** independent **value/unit verification guard** — every number+unit in the answer is
  checked against the cited `Quantity` node; ungrounded claims are flagged. *(Demonstrated live:
  "1 numeric claim, all grounded.")*
- **Mitigate / refuse:** no supporting subgraph → **"cannot answer from the corpus"**, never a
  guess. *(Demonstrated: AirSense 11 unmapped pressure → honest refusal, not a fabrication.)*
- **Citation enforcement:** every fact resolves to source document + page; the UI renders the
  actual PDF page.
- **Monitor / HITL (proposed):** log low-confidence extractions; gate releases on value/unit
  correctness in the eval harness.

---

## Slide 6 — Business outcomes

**Title:** From dozens of manuals to an auditable answer in seconds

- **Faster, safer device comparison & recommendation** with a citation a clinician can verify.
- **Auditable by design** — every numeric claim traces to doc + page; refusals instead of guesses
  reduce clinical risk.
- **Reproducible pipeline** — one CLI/workflow ingests the whole corpus; cheap, idempotent re-runs.
- **Deployed & shareable** — live GraphRAG app (Neo4j Aura + Fly + GitHub Actions CI/CD).
- **Cost-aware** — ingestion is one-off; serving ≈ pennies/query and tunable down via model tier.

---

## Slide 7 — Future improvements

**Title:** From working PoC to production-grade

- **Numeric precision:** controlled-vocab extraction prompt + re-ingest → fix the `other`
  misclassification so all 7 query types answer across devices.
- **Run the evaluation + vector baseline** → publish the GraphRAG-vs-RAG gain per query type.
- **OCR** the one image-only brochure; recover the catalog's CPAP/BiPAP content.
- **Recommendation:** move from transparent heuristic weights to a tuned/learned ranker.
- **Scale path:** queue-based continuous ingestion, telemetry (Sentry/New Relic), Cloudflare/Cloud
  Run hosting (documented in `doc/SCALE_ARCHITECTURE.md`).
- **Productionize entity resolution** with embeddings for fuzzy vendor/model matching.

---

### Speaker notes / framing
- Lead with the **live demo** (DreamStation cited answer + the honest refusal) — it shows both
  correctness and the hallucination guard.
- Be candid in Q&A: the architecture and grounding are solid; the **baseline numbers and full
  numeric coverage are the next milestone**, not yet complete.
