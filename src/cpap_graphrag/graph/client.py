"""Thin Neo4j driver wrapper. Swap the backend here if needed (keeps the rest agnostic)."""
from __future__ import annotations

from typing import Any

from ..config import settings
from ..ontology.schema import SCHEMA_CONSTRAINTS


class GraphClient:
    def __init__(self) -> None:
        from neo4j import GraphDatabase
        self._driver = GraphDatabase.driver(
            settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
        )

    def close(self) -> None:
        self._driver.close()

    def ensure_schema(self) -> None:
        """Create constraints + indexes (idempotent). Run once before loading."""
        for stmt in SCHEMA_CONSTRAINTS:
            self.run(stmt)

    def run(self, cypher: str, **params: Any) -> list[dict]:
        with self._driver.session(database=settings.neo4j_database) as session:
            return [r.data() for r in session.run(cypher, **params)]

    def wipe(self) -> None:
        self.run("MATCH (n) DETACH DELETE n")
