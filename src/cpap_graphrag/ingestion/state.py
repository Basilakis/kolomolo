"""
Ingestion state / dedup — make re-runs cheap and crash-safe.

Each source file is hashed (sha256 of its bytes). On successful ingestion we record a
`(:SourceFile {name, content_hash, ingested_at})` marker node. Before processing a file
we check whether its hash is already marked; if so we skip parse+extract entirely.

This gives us, with no broker:
  - idempotent re-runs: unchanged files are skipped (no duplicate LLM cost)
  - content-addressed change detection: an edited datasheet (new bytes -> new hash) re-ingests
  - crash recovery: re-run after a failure resumes at the first unprocessed file

The marker is a separate label from :Document (the provenance target) to keep concerns clean.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from ..graph.client import GraphClient

_CHUNK = 1 << 20  # 1 MiB


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(_CHUNK), b""):
            h.update(block)
    return h.hexdigest()


_ALREADY = """
MATCH (s:SourceFile {content_hash: $hash})
RETURN count(s) > 0 AS done
"""

_MARK = """
MERGE (s:SourceFile {name: $name})
SET s.content_hash = $hash, s.ingested_at = timestamp()
"""

# Ensures the dedup lookup is indexed (called from GraphClient.ensure_schema via constraints,
# but kept here too so state is self-contained if used standalone).
_INDEX = "CREATE INDEX sourcefile_hash IF NOT EXISTS FOR (s:SourceFile) ON (s.content_hash)"


def ensure_state_index(g: GraphClient) -> None:
    g.run(_INDEX)


def already_ingested(g: GraphClient, content_hash: str) -> bool:
    rows = g.run(_ALREADY, hash=content_hash)
    return bool(rows and rows[0]["done"])


def mark_ingested(g: GraphClient, name: str, content_hash: str) -> None:
    g.run(_MARK, name=name, hash=content_hash)
