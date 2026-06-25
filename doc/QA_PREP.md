# Q&A Prep — defending every decision

> For the live round (10-min talk + 15–20 min Q&A). Each item: the likely question, a crisp
> spoken answer, the alternative you considered, and the trade-off. Lead with the **live demo**
> and the **1.00 vs 0.50 value/unit result** — they're your strongest cards.

---

## 0. The 30-second pitch (say this first)

"Clinicians compare CPAP devices and recommend one per prescription — and the knowledge is
scattered across dozens of vendor PDFs, where the *numbers* matter most. I built a GraphRAG PoC:
ingest the corpus into a Neo4j knowledge graph where every measured value is a typed quantity with
its unit and source page, then serve the seven target query types through an agent that returns
grounded answers with audit-grade citations and refuses when it has no evidence. On the same
corpus, it doubles a vector-only RAG baseline on value/unit correctness — the core business metric."

## ⚠️ One honesty check before you present

`SOLUTION.md`'s diagram describes "Claude reads each page natively (vision+text)". The **implemented**
pipeline actually does **PyMuPDF + pdfplumber text/table extraction → table-aware segmentation →
Claude schema-forced extraction from that text**. If asked "how does extraction work," describe the
implemented version (below). If a reviewer reads the code they'll see text extraction, not vision.
*(Tell me if you want me to either (a) align SOLUTION.md to the implemented text pipeline, or (b)
actually switch to native-PDF reading — both are quick.)*

---

## A. Architecture

**Q: Walk me through the architecture.**
Two planes sharing only the graph. **Offline ingestion:** parse → segment → LLM extraction →
unit normalization → entity resolution → load to Neo4j. **Online serving:** plan (classify into 1
of 7 query types + slot-fill) → route to a fixed Cypher template → grounded answer + citations +
subgraph → refuse if no evidence. They share nothing at runtime but the database.

**Q: Why split them?**
Cost and latency live in different places. Ingestion is LLM-heavy, slow, run once. Serving must be
"seconds, not minutes" and is graph-query bound. Coupling them would drag query latency to
extraction speed. The split lets each be tuned independently — which is exactly what
"query-type optimisation" asks for.

**Q: Why a graph at all — why not just a bigger-context LLM over the PDFs, or plain vector RAG?**
Three of the seven query types are **numeric/relational**: multi-device constraints ("> 20 cmH₂O
**and** < 1.5 kg **and** cellular"), side-by-side comparison, and ranked recommendation. Those are
*joins and numeric filters*, which vector similarity and free-text context cannot do reliably. The
graph stores typed quantities with canonical units, so those become exact Cypher filters. The eval
confirms it: GraphRAG 1.00 vs 0.50 on value/unit correctness.

## B. Graph backend

**Q: Why Neo4j? Why not Postgres/JSON/a vector DB/Kùzu?**
Cypher expresses the multi-hop joins (Device→Quantity→Document, cross-device comparison) far more
naturally than SQL; APOC gave me node-merge for dedup for free; Aura Free hosts it for the live
demo; and the property-graph model maps 1:1 to the domain (devices, parameters, features, modes,
provenance). **Considered:** Kùzu (embedded, simpler, but weaker tooling/ecosystem and no managed
host); a vector DB (can't do numeric joins — it's the baseline we beat); Postgres (works, but the
comparison/constraint queries are graph-shaped).

**Q: Isn't a graph overkill for ~30 devices?**
For the data size, yes a table would store it. The graph earns its place on the **query side** —
the comparison/constraint/recommendation patterns — and on **provenance traversal**. It also scales
cleanly as vendors grow.

## C. Knowledge engineering — ontology & units

**Q: What ontology did you use and why not FHIR?**
A small custom domain ontology **mapped to** schema.org/MedicalDevice (device identity), QUDT
(quantity kinds), and UCUM (unit codes). I deliberately mapped *conceptually* rather than adopt
FHIR `DeviceDefinition` wholesale — FHIR's resource/profile machinery would dominate a PoC with no
retrieval benefit for these seven queries. "Smallest viable schema, extend when retrieval requires."

**Q: "Units are not free text" — how did you actually enforce that?**
Every measured parameter is a first-class `(:Quantity)` node carrying `min/max/default`, the raw
unit, a **UCUM code**, a **QUDT kind**, and an **SI-canonical magnitude**. The canonical magnitude
(e.g. cmH₂O → pascals) is what makes cross-vendor numeric comparison and constraints valid
regardless of the unit a datasheet printed. `pint` does the dimensional conversion; cmH₂O is
registered explicitly since it's the central CPAP unit.

**Q: What if two documents disagree on a value, or use different units?**
Different units are normalised to the same canonical magnitude, so they're directly comparable.
Genuine disagreements currently coexist as separate quantity nodes (each with its own provenance) —
the answer can surface both with their sources. A production step would add a reconciliation/voting
policy; for a PoC, *showing both with citations* is the honest behaviour.

**Q: How do you handle the decimal comma (European datasheets)?**
A defensive guard converts `1,33` → `1.33` before normalization, so e.g. DreamStation's 1.33 kg
parses correctly.

## D. Extraction & data quality

**Q: How does extraction work, and how do you stop it hallucinating numbers?**
Claude is called with a **schema-forced tool** — it *must* return structured JSON where every value
carries `{value|min|max|default, unit, source_doc, page}`. The forced schema is itself the first
hallucination guard: there's no free-text path for a number. I also force a **controlled
`ParameterName` enum** so values land under `pressure_range`, `weight`, `noise_level`… not a generic
bucket — that fix moved most parameters out of `other` and is why spec/constraint queries now answer
across devices.

**Q: A multi-device catalog and a standards PDF are in the corpus — how did you avoid junk?**
A **relevance gate**: extraction tags each entity with `device_type` (cpap/apap/bipap/other); only
PAP therapy devices with a concrete vendor+model persist. Masks, O₂ concentrators, nebulizers,
standards/guideline docs, and `UNKNOWN` entries are dropped. That's why the 117-page distributor
catalog (mostly masks) didn't pollute the graph.

**Q: Entity resolution — "AirSense 11" appears many ways. How?**
Two layers. **Upfront:** vendor-alias normalization (Philips Respironics/Healthcare → Philips, BMC
legal suffixes, GIII→G3, parenthetical/series stripping) groups variants — while *keeping* real
distinguishers (AutoSet/Elite/Pro/10/11) so genuinely different models stay separate. **Self-healing:**
a graph-level dedup pass (APOC merge by normalized key) runs at the end of every ingest, so duplicates
can't accumulate across incremental ingests — the one gap a per-run resolver leaves open.

**Q: Risk of *over*-merging two real models?**
Yes — that's why the normalizer keeps model distinguishers. The conservative choice means I keep
some near-duplicates (e.g. the bare "AirSense 11" alongside "AirSense 11 AutoSet") rather than risk
collapsing AutoSet and Elite. I'd rather under-merge than fabricate a merged device.

## E. Agent & query-type optimisation

**Q: What agent framework — LangGraph? CrewAI? text-to-Cypher?**
A **thin custom agent**, on purpose. The LLM does only what it's reliable at — classify the
question into one of seven types and fill slots under a forced schema. Everything that touches data
is **deterministic routing to a fixed Cypher template** per query type. **Not** free-form
text-to-Cypher (it can hallucinate query logic and is hard to audit) and **not** a general agent
framework (would hide control flow and add dependencies for a bounded 7-type problem). This
determinism is what makes answers auditable — central to the hallucination story.

**Q: Show me the "query-type optimisation" concretely.**
Each type maps to a tuned template: #1/#2 Device→Quantity by controlled parameter (one node answers
unit/range/default); #3 Device→Feature by controlled feature key; #4 multi-device join on a shared
parameter; #5 Device→Mode; #6 numeric+feature filter on **canonical SI** magnitudes; #7 hard-filter
candidates then a transparent weighted score with per-candidate evidence. Indexes on
`canonical_name`, `parameter`, `feature.key`. Device matching is **token-based** (all query tokens
present in the canonical name) so "Philips DreamStation" still matches "Philips Respironics
DreamStation CPAP".

**Q: How does recommendation (#7) rank, and is it explainable?**
Hard constraints (numeric + required features) narrow candidates; then a **transparent weighted
score** adds points for satisfied constraints and desired/lifestyle features (e.g. travel→
travel_friendly, humidification→integrated_humidification), each point carrying its source doc+page.
It's a heuristic, not a learned ranker — deliberately, so every point is auditable. Productionizing
would tune/learn the weights.

## F. Evaluation & baseline

**Q: What metrics did you choose and why?**
**Value/unit correctness as primary** — it *is* the business value ("units are not free text").
Then citation validity (does doc+page resolve), has-citations, latency, and cost/query. The primary
metric is deliberately the hardest and most business-relevant, not generic retrieval P/R.

**Q: Results vs the baseline?**
Same corpus, 7 categories: **value/unit correctness 1.00 (GraphRAG) vs 0.50 (baseline)**. GraphRAG
cites 6/7 and refuses rather than answer weakly; baseline always emits a chunk citation. Trade-off:
GraphRAG is slower (5.7s vs 2.5s p50) and ~4× costlier ($0.105 vs $0.025) — planner + graph + answer
vs a single retrieve+answer.

**Q: Be honest — the baseline tied you on some things. Why is GraphRAG worth it?**
For *simple single-value lookups*, vector RAG is competitive because the answer sits in a
retrievable chunk — I won't pretend otherwise. GraphRAG's decisive edge is **numeric precision and
the structured multi-device queries (#4 comparison, #6 constraints)** that vector similarity
fundamentally can't do — it can't filter "> 20 cmH₂O AND < 1.5 kg" across devices. And it's
auditable and refuses. For a clinical comparison/recommendation tool, precision + provenance beats
speed/cost.

**Q: Interesting that your first eval had GraphRAG *losing*. What happened?**
Great question — it surfaced three real bugs: a Neo4j reserved-keyword collision that crashed
comparison queries, brittle substring device matching (missing "Respironics" → refusal), and a
metric that compared "cmH2O" to "cm H2O" literally. Fixing those flipped value/unit from 0.0 to
1.00. I'd rather show that the eval *caught* real defects than hand-wave a green number.

**Q: Your gold set is small (2 value-checked questions). Isn't that weak?**
Yes — it's the honest limitation. The questions cover all 7 categories but only 2 have numeric gold,
because hand-curating ground truth from the PDFs is the slow part. Expanding the gold set is the
first thing I'd do to strengthen the claim; the harness and baseline are already built for it.

## G. Hallucination assurance (they'll probe this hard)

**Q: How do you prevent, detect, and mitigate hallucinated answers?**
- **Prevent:** schema-forced extraction (no free-text numbers); answers built *only* from returned
  graph nodes; templated Cypher (no hallucinated query logic).
- **Detect:** an independent **value/unit verification guard** re-checks every number+unit in the
  prose against the cited quantity node; ungrounded claims are flagged. Demonstrated live: "1
  numeric claim, all grounded."
- **Mitigate / refuse:** no supporting subgraph → "I can't answer from the corpus", never a guess.
  Demonstrated live: an unmapped device returned an honest refusal, not a fabrication.
- **Enforce citations:** every fact resolves to source doc + page; the UI renders the actual PDF page.
- **Monitor / HITL (proposed):** log low-confidence extractions; gate releases on value/unit
  correctness in the eval.

**Q: The verifier uses the same model — isn't that marking its own homework?**
The verifier isn't an LLM — it's **deterministic code** that extracts number+unit patterns from the
answer and checks set-membership against the evidence quantities. So it's an independent check, not
the model judging itself.

## H. LLM & cost

**Q: Why Claude, and why Opus for extraction?**
Reliable forced-tool JSON output (critical for the schema-forced contract) and strong document
reading. **Opus for extraction** because extraction errors are *permanent* — they poison the graph
and every downstream answer — so I pay for accuracy where it compounds. Serving uses a cheaper tier
(the eval ran on Haiku) because the graph already did the hard work; the model just classifies and
phrases. Everything is one env var to swap.

**Q: What does it cost?**
Ingestion is a one-off ~few-dollar Opus run over 28 PDFs (idempotent — content-hash dedup means
unchanged files are never re-extracted). Serving ≈ pennies/query and tunable down by model tier.

**Q: Data residency / PHI — sending datasheets to a hosted API?**
The corpus is vendor datasheets (not patient data), so no PHI in ingestion. For a real deployment
with patient context in recommendations, the design supports swapping the hosted API for a
**self-hosted model server** (vLLM/Ollama) behind the same schema-forced contract — only the client
base-URL changes.

## I. Deployment & scale

**Q: The brief said local PoC — why did you deploy to the cloud?**
The brief is satisfied locally (docker-compose + CLI). I added a hosted demo (Neo4j Aura + Fly +
GitHub Actions CI/CD) purely so it's *shareable and verifiable* — and to dogfood the ingestion as a
repeatable workflow. It's explicitly beyond scope, noted as such in SOLUTION.md.

**Q: How would this scale to continuous, multi-source ingestion?**
The offline/online split means I add a **message queue** (Celery + RabbitMQ/SQS) with retries, a
DLQ, and rate-limit-aware prefetch *behind the same graph contract* — serving is untouched. Neo4j
goes to a causal cluster with read replicas for the (read-only) query path. Observability:
OpenTelemetry traces, Sentry for failed/poison tasks, New Relic for queue depth/throughput +
autoscaling. The idempotency (content-hash + MERGE) that's already in the PoC is what makes
at-least-once queue delivery safe. (Documented in `doc/SCALE_ARCHITECTURE.md`.)

**Q: Why not Cloudflare Workers?**
It's a stateful Python/Streamlit + Neo4j-Bolt + native-deps stack — incompatible with the
Workers WASM/edge model. Cloudflare's role would be a Tunnel (front a local run) or Containers, not
Workers. (Analysis in the deployment docs.)

## J. Coverage & limitations (own them before they ask)

- **All 28 PDFs processed; 23 produced device data.** 5 didn't: 1 image-only brochure (OCR deferred
  by design), the non-CPAP distributor catalog (relevance-gated — correct), and 3 extraction misses
  I'd chase next.
- **OCR deferred** for the one image-only file (issue #1) — a conscious scope call, not an oversight.
- **Numeric precision is now good but not perfect** — some specs still land under `other`; the gold
  set is small; recommendation weights are heuristic.
- **GraphRAG is slower/costlier than the baseline** — inherent, and acceptable for a precision-first
  clinical tool.
- **Device variants** (AirSense 11 AutoSet/Elite/CPAP) inflate the device count — mostly legitimate,
  some residual near-duplicates from conservative dedup.

**Q: What would you do next, in order?**
1. Expand the eval gold set + add per-category correctness (where GraphRAG's #6 edge shows hardest).
2. Reconcile the extraction story (native-PDF vision *or* align docs to the text pipeline).
3. OCR the image-only doc; recover the catalog's CPAP content.
4. Learned recommendation ranker; embedding-based entity resolution.
5. The queue + observability scale path.

## K. Curveballs (short, confident answers)

- **"What if I ask about a device not in the corpus?"** → It refuses with "no evidence in the
  corpus" — by design, no guessing.
- **"Ambiguous question?"** → The planner classifies + slot-fills; if it can't map to a query type
  it returns `unsupported` and refuses.
- **"How do you know a citation is real?"** → The eval's citation-validity metric checks every cited
  (doc, page) resolves against the inventory; the UI renders the page.
- **"Two devices, same model name, different vendors?"** → Key is (normalized vendor, normalized
  model), so they stay distinct.
- **"Why 87 'devices' from ~30 models?"** → Legitimate sub-variants (AutoSet/Elite/Pro) plus a few
  conservative-dedup residuals; I chose under-merge over false-merge.
- **"Could the LLM extract a wrong page number?"** → Provenance (doc+page) is passed *into* the
  extraction context per segment, not invented by the model — it's deterministic from the parse.
- **"Latency 5.7s — too slow?"** → Dominated by two LLM calls (plan + answer); drop to a faster tier
  or cache hot lookups to hit sub-2s; the graph query itself is milliseconds.

## L. Quick-reference numbers (memorise these)

- Corpus: **28 PDFs**, ~10 vendors; 23 produced device data.
- Graph: **~87 device entries, ~190 quantities, 15 features, 23 modes, 23 docs** (Neo4j Aura).
- Parameter mapping after fix: pressure_range ×35, weight ×38, ramp_time ×21, noise_level ×20.
- **Eval: value/unit correctness 1.00 vs 0.50; citations 0.86 vs 1.00; latency 5.7s vs 2.5s; cost
  $0.105 vs $0.025/query.**
- Live demo line: *"What is the pressure range of the DreamStation?"* → **4–20 cm H₂O
  [DreamStation_CPAP_Pro_DataSheet.pdf p.2]**, value/unit guard passed.
