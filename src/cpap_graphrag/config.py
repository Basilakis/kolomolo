"""Central configuration, loaded from environment (.env)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    # LLM
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    extraction_model: str = os.getenv("EXTRACTION_MODEL", "claude-opus-4-8")
    agent_model: str = os.getenv("AGENT_MODEL", "claude-sonnet-4-6")

    # Neo4j
    neo4j_uri: str = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    neo4j_user: str = os.getenv("NEO4J_USER", "neo4j")
    neo4j_password: str = os.getenv("NEO4J_PASSWORD", "sleepmedcorp")
    neo4j_database: str = os.getenv("NEO4J_DATABASE", "neo4j")

    # Paths
    data_zip: Path = ROOT / os.getenv("DATA_ZIP", "data/cpap-datasheets-and-manuals.zip")
    data_dir: Path = ROOT / os.getenv("DATA_DIR", "data/corpus")

    # Baseline
    chroma_dir: Path = ROOT / os.getenv("CHROMA_DIR", ".chroma")
    embed_model: str = os.getenv("EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")


settings = Settings()
