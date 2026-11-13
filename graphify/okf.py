"""Read Open Knowledge Format bundles into a Graphify graph.

The reader is deliberately best-effort, as OKF requires consumers to tolerate
unknown fields and broken links. It preserves the complete frontmatter on each
node while mapping standard concepts and Markdown relationships onto NetworkX.
"""

from __future__ import annotations

import json
import posixpath
import re
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from typing import Any

import networkx as nx
import yaml
from networkx.readwrite import json_graph


MAX_FILES = 50_000
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_YAML_NODES = 20_000
_LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
_TOKENSAVE_LINK = re.compile(r"^\s*[*-]\s+\[[^\]]+\]\(([^)]+)\)\s+-\s+(.+?)\s*$")
_EDGE_KIND = re.compile(r"([^,]+?)\s+×(\d+)(?:,|$)")


def _json_value(
    value: Any,
    active: set[int] | None = None,
    count: list[int] | None = None,
) -> Any:
    """Return metadata that NetworkX can serialize as node-link JSON."""
    active = active if active is not None else set()
    count = count if count is not None else [0]
    count[0] += 1
    if count[0] > MAX_YAML_NODES:
        raise ValueError(f"OKF frontmatter exceeds {MAX_YAML_NODES} expanded nodes")
    if isinstance(value, (dict, list)):
        identity = id(value)
        if identity in active:
            raise ValueError("OKF frontmatter contains a cyclic YAML alias")
        active.add(identity)
        try:
            if isinstance(value, dict):
                return {
                    str(key): _json_value(item, active, count)
                    for key, item in value.items()
                }
            return [_json_value(item, active, count) for item in value]
        finally:
            active.remove(identity)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _document(text: str, path: Path) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        raise ValueError(f"OKF concept has no YAML frontmatter: {path}")
    end = text.find("\n---", 4)
    if end < 0:
        raise ValueError(f"OKF concept has unterminated YAML frontmatter: {path}")
    loaded = yaml.safe_load(text[4:end]) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"OKF concept frontmatter is not a mapping: {path}")
    return _json_value(loaded), text[end + 4 :].lstrip("\r\n")


def _concept_id(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()[:-3]


def _target_id(source_id: str, target: str) -> str | None:
    target = target.split("#", 1)[0].split("?", 1)[0]
    if not target or "://" in target or target.startswith("#"):
        return None
    if target.startswith("/"):
        normalized = posixpath.normpath(target.lstrip("/"))
    else:
        normalized = posixpath.normpath(str(PurePosixPath(source_id).parent / target))
    if normalized == ".." or normalized.startswith("../") or not normalized.endswith(".md"):
        return None
    return normalized[:-3]


def _file_type(frontmatter: dict[str, Any]) -> str:
    explicit = str(frontmatter.get("graphify_file_type") or "").strip()
    if explicit:
        return explicit
    concept_type = str(frontmatter.get("type") or "").casefold()
    if concept_type == "source file":
        return "code"
    if "document" in concept_type or "report" in concept_type:
        return "document"
    return "concept"


def _source_file(frontmatter: dict[str, Any]) -> str | None:
    for key in ("tokensave_path", "graphify_source_file", "resource"):
        value = frontmatter.get(key)
        if isinstance(value, str) and value and "://" not in value and not value.startswith("/"):
            return value.replace("\\", "/")
    return None


def from_okf_bundle(bundle: str | Path) -> nx.DiGraph:
    """Load searchable OKF concepts and their links into a directed graph."""
    root = Path(bundle).resolve()
    if not root.is_dir():
        raise ValueError(f"OKF bundle is not a directory: {root}")

    paths: list[Path] = []
    total_bytes = 0
    for path in root.rglob("*.md"):
        relative = path.relative_to(root)
        if path.is_symlink() or any(part.startswith(".") for part in relative.parts):
            continue
        if path.name in {"index.md", "log.md"}:
            continue
        if len(paths) >= MAX_FILES:
            raise ValueError(f"OKF bundle exceeds {MAX_FILES} concept files")
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            raise ValueError(f"OKF concept exceeds {MAX_FILE_BYTES} bytes: {relative}")
        total_bytes += size
        if total_bytes > MAX_TOTAL_BYTES:
            raise ValueError(f"OKF bundle exceeds {MAX_TOTAL_BYTES} bytes")
        paths.append(path)

    graph = nx.DiGraph(okf_bundle=str(root))
    bodies: dict[str, str] = {}
    for path in sorted(paths):
        text = path.read_text(encoding="utf-8")
        frontmatter, body = _document(text, path)
        concept_id = _concept_id(root, path)
        concept_type = str(frontmatter.get("type") or "").strip()
        if not concept_type:
            continue
        title = str(frontmatter.get("title") or concept_id.rsplit("/", 1)[-1])
        graph.add_node(
            concept_id,
            label=title,
            file_type=_file_type(frontmatter),
            source_file=_source_file(frontmatter),
            description=frontmatter.get("description"),
            tags=frontmatter.get("tags", []),
            okf_id=concept_id,
            okf_type=concept_type,
            okf_frontmatter=frontmatter,
            okf_body=body,
        )
        bodies[concept_id] = body

    for source_id, body in bodies.items():
        heading = ""
        for line in body.splitlines():
            if line.startswith("#"):
                heading = line.lstrip("#").strip().casefold()
                continue
            match = _TOKENSAVE_LINK.match(line) if heading == "dependencies" else None
            if match:
                target_id = _target_id(source_id, match.group(1))
                if target_id not in graph:
                    continue
                kinds = {
                    kind.strip(): int(count)
                    for kind, count in _EDGE_KIND.findall(match.group(2))
                }
                relation = next(iter(kinds), "depends_on")
                graph.add_edge(
                    source_id,
                    target_id,
                    relation=relation,
                    confidence="EXTRACTED",
                    tokensave_edge_kinds=kinds,
                    _src=source_id,
                    _tgt=target_id,
                )
                continue
            # TokenSave writes the same file relationship a second time under
            # the target's Dependents section. The Dependencies entry above is
            # authoritative and preserves its direction.
            if heading == "dependents":
                continue
            for target in _LINK.findall(line):
                target_id = _target_id(source_id, target)
                if target_id not in graph or graph.has_edge(source_id, target_id):
                    continue
                relation = heading.replace(" ", "_") or "related"
                graph.add_edge(
                    source_id,
                    target_id,
                    relation=relation,
                    confidence="EXTRACTED",
                    _src=source_id,
                    _tgt=target_id,
                )
    return graph


def write_graph(graph: nx.Graph, output: str | Path) -> Path:
    """Write a Graphify-compatible node-link graph, replacing ``output``."""
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json_graph.node_link_data(graph, edges="links")
    except TypeError:
        data = json_graph.node_link_data(graph)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path
