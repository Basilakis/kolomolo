# SleepMedCorp — CPAP Knowledge Graph & GraphRAG

An architect-level proof-of-concept that ingests a corpus of CPAP/BiPAP/APAP vendor
manuals and datasheets into a **knowledge graph**, then serves device **comparison** and
patient-tailored **recommendation** questions through a **GraphRAG** agent with
audit-grade citations (source document + page).

> Optimised for seven target query types (see `SOLUTION.md`): spec lookup, parameter/unit/range/default,
> feature lookup, differentiator/comparison, mode & indication, multi-device numeric constraint,
> and patient-tailored recommendation.

## Quickstart

```bash
# 1. Create + activate a virtual env (Windows PowerShell)
python -m venv .venv; .\.venv\Scripts\Activate.ps1

# 2. Install
pip install -r requirements.txt

# 3. Start the graph backend (Neo4j) + copy env
docker compose up -d
copy .env.example .env   # then set ANTHROPIC_API_KEY

# 4. Place the data package
#    Put cpap-datasheets-and-manuals.zip in ./data/  (it is unzipped automatically)

# 5. Inventory the corpus (no LLM calls — fast, do this first)
python -m cpap_graphrag.ingestion.cli inventory

# 6. Run full ingestion (parse -> segment -> extract -> resolve -> load)
python -m cpap_graphrag.ingestion.cli ingest

# 7. Evaluate GraphRAG vs the vector-only baseline
python -m cpap_graphrag.eval.run_eval

# 8. Launch the app
streamlit run app/streamlit_app.py
```

## Repository layout

```
src/cpap_graphrag/
  ontology/      domain schema + UCUM/QUDT unit normalization ("units are not free text")
  ingestion/     parse -> segment -> extract -> resolve -> load pipeline + CLI
  graph/         Neo4j client + Cypher templates tuned per query type
  agent/         query planner, graph tools, grounded-answer/citation layer
  baseline/      vector-only RAG (the GraphRAG comparison floor)
  eval/          curated 7-category question set, metrics, runner
app/             Streamlit frontend (answer + supporting subgraph + citations)
data/            drop the vendor zip here
```

See **`SOLUTION.md`** for architecture, ontology/unit modelling, query-type optimisation,
evaluation metrics, baseline comparison, hallucination assurance, and trade-offs.
