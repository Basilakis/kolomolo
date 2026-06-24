"""
Graph-level device de-duplication — the self-healing safeguard for issue #7.

`resolve.py` consolidates duplicates *within one ingest run*, but `load` MERGEs devices by
exact `canonical_name`. So an INCREMENTAL ingest (a new doc adding a naming variant of a
device already in the graph) would create a duplicate node. This pass re-groups EVERY device
in the graph by its normalized canonical key and merges duplicates via APOC — so duplicates
can never accumulate, no matter how many separate ingests run.

Runs automatically at the end of `cli ingest`, and is also exposed as `cli dedup`.
Requires APOC (present on Neo4j Aura and in our docker-compose).
"""
from __future__ import annotations

from collections import defaultdict

from ..graph.client import GraphClient
from .resolve import _norm_model, _norm_vendor

_ALL_DEVICES = """
MATCH (d:Device)
OPTIONAL MATCH (d)-[:HAS_PARAMETER]->(q:Quantity)
RETURN d.canonical_name AS name, d.vendor AS vendor, d.model AS model, count(q) AS params
"""

_MERGE = """
MATCH (keep:Device {canonical_name: $keep})
MATCH (dup:Device) WHERE dup.canonical_name IN $dups AND dup <> keep
WITH keep, collect(dup) AS dups
WHERE size(dups) > 0
CALL apoc.refactor.mergeNodes([keep] + dups, {properties: 'discard', mergeRels: true})
YIELD node
RETURN node.canonical_name AS merged
"""


def dedup_graph(client: GraphClient | None = None) -> int:
    """Merge duplicate Device nodes by normalized key. Returns the number of nodes removed."""
    client = client or GraphClient()
    rows = client.run(_ALL_DEVICES)

    groups: dict[tuple[str, str], list[tuple[int, str]]] = defaultdict(list)
    for r in rows:
        key = (_norm_vendor(r["vendor"] or ""), _norm_model(r["model"] or "", r["vendor"] or ""))
        groups[key].append((r["params"], r["name"]))

    removed = 0
    for names in groups.values():
        if len(names) < 2:
            continue
        # keep the richest node (most parameters) as canonical; merge the rest into it
        names.sort(reverse=True)                       # by param count desc
        keep = names[0][1]
        dups = [n for _, n in names[1:]]
        client.run(_MERGE, keep=keep, dups=dups)
        removed += len(dups)
    return removed
