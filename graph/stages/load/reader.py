"""Reading `nodes.jsonl` and `edges.jsonl` strictly enough that the loader cannot be surprised.

One responsibility: turn the two export artifacts into `GraphNode` / `GraphEdge` values, or
refuse. Nothing here opens a connection, and the refusal is the point — every check below runs
**before** `loader.py` is allowed to send a single row, because a property Neo4j cannot store
is discovered by the driver halfway through batch 19 otherwise, with eighteen batches already
committed. The whole file is read and validated up front for exactly that reason; reading both
artifacts of the real export costs 2.0 s and a 290 MB peak RSS *(measured 2026-08-03: 28,836
nodes, 35,603 edges)*, which is affordable, and streaming would trade it for the guarantee this
module exists to make.

**What Neo4j can hold as a property**, and therefore what this reader accepts *(verified
2026-08-03 against `data/graph_runs/graph-v1-886059d862ce/`, 28,836 nodes and 35,603 edges)*:

    scalar            `str`, `bool`, `int`, `float`      502,499 / 17,194 / 22,260 / 2,707 node
                                                         values; 405,593 / 52,731 / 0 / 0 edge
    homogeneous list  one primitive type, or empty       10,115 all-`str` lists and 21,350 empty
                                                         lists on nodes; 2,713 all-`str` on edges
    map               *refused* — a Neo4j property is    0 in the export
                      never a nested structure
    null              *refused* — G1 drops null-valued   0 in the export, and re-introducing one
                      properties (`SET n += {k: null}`   would delete a key rather than store it
                      removes the key)

The measured columns are why the rules are written this narrowly rather than defensively: a
heterogeneous list and a nested map do not occur, so accepting them "just in case" would mean
guessing at a coercion nobody asked for. `MALFORMED_EVENT_PROPERTY` (§6.4) is the projection's
name for the same refusal on the way in; this is the same check on the way out, because an
export that was written by a different version of the projection is still an untrusted file.

Integers are bounded at ±2^63 because a Neo4j INTEGER is 64-bit signed and Python's is not —
the largest in the export is 9,793, so the bound has never been reached, and a violation would
otherwise surface as a driver-side error after the batch had begun.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from pydantic import ValidationError

from graph.core.models import GraphEdge, GraphNode

NODES_FILENAME = "nodes.jsonl"
EDGES_FILENAME = "edges.jsonl"

#: A Neo4j INTEGER is 64-bit signed. Python's is arbitrary precision, so the check belongs here.
NEO4J_INT_MIN = -(2**63)
NEO4J_INT_MAX = 2**63 - 1


@dataclass(frozen=True)
class ExportContents:
    """Both artifacts of one graph run, read and checked.

    Deliberately **not** `graph.core.models.GraphExport`: that type carries a
    `projection_version` field whose default is *this code's* constant, so returning one would
    have the reader assert a version it never read. The version a file on disk was written by
    is recorded in its `manifest.json` and on every row as `graph_projection_version`; the
    reader reports rows, not provenance.
    """

    directory: Path
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]


class GraphExportReadError(ValueError):
    """A row the export should not contain, named by file, line, node/edge key and property.

    Carries the location as fields rather than only in the message so a caller can report a
    hundred bad rows in a table instead of a hundred strings.
    """

    def __init__(
        self,
        *,
        path: Path,
        line_number: int,
        detail: str,
        key: str | None = None,
        property_name: str | None = None,
    ) -> None:
        self.path = path
        self.line_number = line_number
        self.detail = detail
        self.key = key
        self.property_name = property_name
        location = f"{path.name}:{line_number}"
        if key is not None:
            location += f" ({key})"
        super().__init__(f"{location}: {detail}")


def _reject_json_constant(name: str) -> Any:
    """`json.loads` accepts `NaN`, `Infinity` and `-Infinity` by default; none is legal JSON."""
    raise ValueError(f"{name} is not valid JSON and is not a value the projection emits")


def _type_name(value: Any) -> str:
    """Neo4j's vocabulary for a value's type, not Python's — the message is about storage."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, dict):
        return "map"
    if isinstance(value, list):
        return "list"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "string"
    return type(value).__name__


def _scalar_fault(value: Any) -> str | None:
    """Why `value` cannot be a Neo4j scalar, or `None` if it can."""
    if isinstance(value, bool) or isinstance(value, (str, float)):
        return None
    if isinstance(value, int):
        if not NEO4J_INT_MIN <= value <= NEO4J_INT_MAX:
            return f"integer {value} is outside Neo4j's signed 64-bit range"
        return None
    return f"a {_type_name(value)} is not a value Neo4j can store as a property"


def property_fault(name: str, value: Any) -> str | None:
    """Why `name: value` cannot be written to Neo4j, or `None` if it can.

    Public because `loader.py`'s guarantee is stated in terms of it: nothing is sent until
    every property of every row has been through this function.
    """
    if value is None:
        return (
            f"property {name!r} is null; the export carries no null-valued properties, because "
            "`SET n += {k: null}` removes the key rather than storing it"
        )
    if isinstance(value, dict):
        return (
            f"property {name!r} is a map; a Neo4j property is a scalar or a homogeneous list "
            "of one primitive type, never a nested structure"
        )
    if isinstance(value, list):
        kinds: set[str] = set()
        for index, element in enumerate(value):
            if element is None or isinstance(element, (dict, list)):
                return (
                    f"property {name!r} holds a {_type_name(element)} at index {index}; a Neo4j "
                    "array holds primitives only"
                )
            fault = _scalar_fault(element)
            if fault is not None:
                return f"property {name!r} at index {index}: {fault}"
            kinds.add(_type_name(element))
        if len(kinds) > 1:
            return (
                f"property {name!r} is a list mixing {', '.join(sorted(kinds))}; a Neo4j array "
                "holds one primitive type"
            )
        return None
    return None if (fault := _scalar_fault(value)) is None else f"property {name!r}: {fault}"


def _check_properties(
    properties: dict[str, Any], *, path: Path, line_number: int, key: str
) -> None:
    for name, value in properties.items():
        if not isinstance(name, str) or not name.strip():
            raise GraphExportReadError(
                path=path,
                line_number=line_number,
                key=key,
                detail=f"property name {name!r} is empty",
            )
        fault = property_fault(name, value)
        if fault is not None:
            raise GraphExportReadError(
                path=path,
                line_number=line_number,
                key=key,
                property_name=name,
                detail=fault,
            )


def _describe(error: ValidationError) -> str:
    """A pydantic failure in the export's own vocabulary — "unknown field", not "extra_forbidden".

    Only the first problem is reported: the row is already refused, and a caller repairing an
    export fixes one field at a time anyway.
    """
    first = error.errors()[0]
    location = ".".join(str(part) for part in first["loc"]) or "<row>"
    kind = first["type"]
    if kind == "extra_forbidden":
        return f"unknown field {location!r}"
    if kind == "missing":
        return f"missing field {location!r}"
    return f"field {location!r}: {first['msg']}"


def _iter_rows(path: Path) -> Iterator[tuple[int, dict[str, Any]]]:
    if not path.is_file():
        raise GraphExportReadError(
            path=path, line_number=0, detail=f"{path} does not exist"
        )
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                raise GraphExportReadError(
                    path=path, line_number=line_number, detail="blank line"
                )
            try:
                payload = json.loads(line, parse_constant=_reject_json_constant)
            except ValueError as exc:
                raise GraphExportReadError(
                    path=path, line_number=line_number, detail=f"unreadable JSON: {exc}"
                ) from exc
            if not isinstance(payload, dict):
                raise GraphExportReadError(
                    path=path,
                    line_number=line_number,
                    detail=f"row is a {_type_name(payload)}, expected an object",
                )
            yield line_number, payload


def read_nodes(path: Path) -> tuple[GraphNode, ...]:
    """Every row of `nodes.jsonl`, or the first reason there is no answer.

    The duplicate check is not redundant with §5.2's uniqueness constraint: a second row
    carrying a key the first row already used does not *violate* the constraint — it `MERGE`s
    onto the same node and silently overwrites it, which is a dropped row wearing a successful
    load's clothing. Measured 0 duplicates in the real export.
    """
    nodes: list[GraphNode] = []
    seen: dict[tuple[str, str], int] = {}
    for line_number, payload in _iter_rows(path):
        try:
            node = GraphNode.model_validate(payload)
        except ValidationError as exc:
            raise GraphExportReadError(
                path=path,
                line_number=line_number,
                key=payload.get("key") if isinstance(payload.get("key"), str) else None,
                detail=_describe(exc),
            ) from exc
        _check_properties(
            node.properties, path=path, line_number=line_number, key=node.key
        )
        identity = (node.base_label, node.key)
        if identity in seen:
            raise GraphExportReadError(
                path=path,
                line_number=line_number,
                key=node.key,
                detail=(
                    f":{node.base_label} key {node.key!r} already appeared on line "
                    f"{seen[identity]}; a repeated key merges onto one node and loses a row"
                ),
            )
        seen[identity] = line_number
        nodes.append(node)
    return tuple(nodes)


def read_edges(path: Path) -> tuple[GraphEdge, ...]:
    """Every row of `edges.jsonl`, or the first reason there is no answer.

    `edge_key` is what the loader `MERGE`s on (§4.1), so a repeat would be two rows collapsing
    into one relationship — the same silent loss `read_nodes` refuses. Measured 0 in the real
    export.
    """
    edges: list[GraphEdge] = []
    seen: dict[str, int] = {}
    for line_number, payload in _iter_rows(path):
        try:
            edge = GraphEdge.model_validate(payload)
        except ValidationError as exc:
            key = payload.get("edge_key")
            raise GraphExportReadError(
                path=path,
                line_number=line_number,
                key=key if isinstance(key, str) else None,
                detail=_describe(exc),
            ) from exc
        _check_properties(
            edge.properties, path=path, line_number=line_number, key=edge.edge_key
        )
        if edge.edge_key in seen:
            raise GraphExportReadError(
                path=path,
                line_number=line_number,
                key=edge.edge_key,
                detail=(
                    f"edge_key {edge.edge_key!r} already appeared on line "
                    f"{seen[edge.edge_key]}; a repeated key merges onto one relationship"
                ),
            )
        seen[edge.edge_key] = line_number
        edges.append(edge)
    return tuple(edges)


def read_export(directory: Path) -> ExportContents:
    """Both artifacts of one graph run, in the order the loader needs them."""
    return ExportContents(
        directory=directory,
        nodes=read_nodes(directory / NODES_FILENAME),
        edges=read_edges(directory / EDGES_FILENAME),
    )


__all__ = [
    "EDGES_FILENAME",
    "ExportContents",
    "GraphExportReadError",
    "NEO4J_INT_MAX",
    "NEO4J_INT_MIN",
    "NODES_FILENAME",
    "property_fault",
    "read_edges",
    "read_export",
    "read_nodes",
]
