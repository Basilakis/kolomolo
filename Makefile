# One-command workflows. On Windows, run these from Git Bash (make is bundled) or use the
# raw commands shown in doc/DEPLOYMENT.md. Targets assume the venv is active.

.PHONY: help setup up down logs inventory ingest eval index-baseline app app-docker scale clean

help:           ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

setup:          ## Create venv, install deps (editable), copy .env
	python -m venv .venv
	. .venv/bin/activate && pip install -e .
	cp -n .env.example .env || true
	@echo "Edit .env and set ANTHROPIC_API_KEY"

up:             ## Start Neo4j (PoC core)
	docker compose up -d neo4j

down:           ## Stop all containers
	docker compose down

logs:           ## Tail Neo4j logs
	docker compose logs -f neo4j

inventory:      ## Unzip + inventory the corpus (run first, no LLM)
	python -m cpap_graphrag.ingestion.cli inventory

ingest:         ## Full ingestion (parse->extract->resolve->load). Vars: WORKERS, LIMIT, FORCE
	python -m cpap_graphrag.ingestion.cli ingest \
	  $(if $(WORKERS),--workers $(WORKERS),) \
	  $(if $(LIMIT),--limit $(LIMIT),) \
	  $(if $(FORCE),--force,)

index-baseline: ## Build the vector-only baseline index over the same corpus
	python -c "from cpap_graphrag.baseline.vector_rag import build_index; print(build_index(), 'chunks')"

eval:           ## Run GraphRAG vs baseline evaluation
	python -m cpap_graphrag.eval.run_eval

app:            ## Run the Streamlit app on the host
	streamlit run app/streamlit_app.py

app-docker:     ## Run Neo4j + app in containers
	docker compose --profile app up -d --build

scale:          ## Start the scale preview (Neo4j + Redis broker + worker)
	docker compose --profile scale up -d --build

clean:          ## Remove containers + volumes (DESTROYS the graph)
	docker compose down -v
