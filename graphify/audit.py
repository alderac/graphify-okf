from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from typing import Any


STRICT_FATAL_CODES = {
    "semantic_cache_miss",
    "missing_source_file",
    "invalid_json",
    "hollow_response",
    "partial_truncation",
    "chunk_failed",
    "node_id_collision",
    "unparseable_label_batch",
}

LOW_SEVERITY_CODES = {
    "community_label_fallback",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _posix(path: str | Path) -> str:
    return Path(path).as_posix()


def new_extraction_audit(target: Path, out_root: Path, mode: str, strict: bool) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "mode": mode,
        "strict": strict,
        "target": str(Path(target).resolve()),
        "out_root": str(Path(out_root).resolve()),
        "started_at": _now(),
        "finished_at": None,
        "backend": {
            "name": None,
            "model": None,
            "base_url": None,
            "route": None,
            "native_structured_output": False,
            "fell_back_to_openai_compat": False,
        },
        "detected": {
            "code_files": 0,
            "document_files": 0,
            "paper_files": 0,
            "image_files": 0,
            "semantic_files": [],
        },
        "cache": {
            "semantic_inputs": 0,
            "semantic_cache_hits": 0,
            "semantic_cache_misses": 0,
            "hit_paths": [],
            "miss_paths": [],
        },
        "extraction": {
            "input_tokens": 0,
            "output_tokens": 0,
            "failed_files": [],
            "retries": 0,
            "partial_truncations": 0,
        },
        "warnings": [],
        "collisions": [],
        "source_attribution_violations": [],
        "strict_failures": [],
    }


def finish_audit(audit: dict[str, Any]) -> None:
    audit["finished_at"] = _now()
    audit["strict_failures"] = strict_failures(audit)


def add_warning(
    audit: dict[str, Any],
    code: str,
    message: str,
    severity: str = "warning",
    source_file: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    audit.setdefault("warnings", []).append(
        {
            "code": code,
            "severity": "info" if code in LOW_SEVERITY_CODES else severity,
            "message": message,
            "source_file": source_file,
            "details": details or {},
        }
    )


def record_backend(audit: dict[str, Any], info: dict[str, Any]) -> None:
    audit["backend"].update(
        {
            "name": info.get("name"),
            "model": info.get("model"),
            "base_url": info.get("base_url"),
            "route": info.get("route"),
            "native_structured_output": bool(info.get("native_structured_output")),
            "fell_back_to_openai_compat": bool(info.get("fell_back_to_openai_compat")),
        }
    )


def record_detection(
    audit: dict[str, Any],
    *,
    code_files: list[Path],
    doc_files: list[Path],
    paper_files: list[Path],
    image_files: list[Path],
) -> None:
    semantic = [*doc_files, *paper_files, *image_files]
    audit["detected"] = {
        "code_files": len(code_files),
        "document_files": len(doc_files),
        "paper_files": len(paper_files),
        "image_files": len(image_files),
        "semantic_files": [_posix(p) for p in semantic],
    }


def record_cache_status(
    audit: dict[str, Any],
    semantic_inputs: list[str],
    semantic_cache_hits: list[str],
    semantic_cache_misses: list[str],
) -> None:
    audit["cache"] = {
        "semantic_inputs": len(semantic_inputs),
        "semantic_cache_hits": len(semantic_cache_hits),
        "semantic_cache_misses": len(semantic_cache_misses),
        "hit_paths": [_posix(p) for p in semantic_cache_hits],
        "miss_paths": [_posix(p) for p in semantic_cache_misses],
    }
    for path in semantic_cache_misses:
        add_warning(
            audit,
            "semantic_cache_miss",
            f"semantic cache miss for {path}",
            source_file=_posix(path),
        )


def find_source_attribution_violations(extraction: dict[str, Any]) -> list[dict[str, Any]]:
    violations: list[dict[str, Any]] = []
    for i, node in enumerate(extraction.get("nodes", []) or []):
        if isinstance(node, dict) and not node.get("source_file"):
            violations.append({"kind": "node", "index": i, "id": str(node.get("id", "?"))})

    for edge_key in ("edges", "links"):
        edge_list = extraction.get(edge_key)
        for i, edge in enumerate(edge_list or []):
            if isinstance(edge, dict) and not edge.get("source_file"):
                src = edge.get("source", edge.get("from", "?"))
                tgt = edge.get("target", edge.get("to", "?"))
                violations.append({"kind": "edge", "index": i, "id": f"{src}->{tgt}"})

    for i, hyperedge in enumerate(extraction.get("hyperedges", []) or []):
        if isinstance(hyperedge, dict) and not hyperedge.get("source_file"):
            violations.append({"kind": "hyperedge", "index": i, "id": str(hyperedge.get("id", "?"))})

    return violations


def backfill_single_file_source(extraction: dict[str, Any], source_file: str) -> None:
    for bucket in ("nodes", "edges", "links", "hyperedges"):
        for item in extraction.get(bucket, []) or []:
            if isinstance(item, dict) and not item.get("source_file"):
                item["source_file"] = source_file


def find_node_id_collisions(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for node in nodes:
        if isinstance(node, dict) and node.get("id"):
            grouped[str(node["id"])].append(node)
    reports: list[dict[str, Any]] = []
    for node_id, group in grouped.items():
        sources = sorted({str(n.get("source_file") or "") for n in group if n.get("source_file")})
        if len(sources) <= 1:
            continue
        labels = sorted({str(n.get("label") or "") for n in group if n.get("label")})
        reports.append({"id": node_id, "sources": sources, "labels": labels, "count": len(group)})
    return reports


def _source_hash(source_file: str, root: Path) -> str:
    try:
        from graphify.cache import file_hash

        p = Path(source_file)
        if not p.is_absolute():
            p = root / p
        if p.is_file():
            return file_hash(p, root)[:8]
    except OSError:
        pass
    return hashlib.sha256(str(source_file).encode("utf-8", errors="replace")).hexdigest()[:8]


def _source_stem(source_file: str, root: Path) -> str:
    from graphify.extractors.base import _file_stem

    p = Path(source_file)
    try:
        rel = p.resolve().relative_to(root.resolve()) if p.is_absolute() else p
    except (ValueError, OSError):
        rel = p
    return _file_stem(rel)


def namespace_semantic_node_ids(
    extraction: dict[str, Any],
    root: Path,
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """Namespace semantic node IDs by source path and content hash.

    LLM output often names same-label concepts with the same ID across files.
    Before graph build, make those IDs source-specific so NetworkX cannot
    silently merge the second node into the first. Repeated calls are safe for
    cached results that were already written after namespacing.
    """
    from graphify.ids import make_id, normalize_id

    nodes = [n for n in extraction.get("nodes", []) or [] if isinstance(n, dict)]
    collisions = find_node_id_collisions(nodes)
    remap_by_source: dict[tuple[str, str], str] = {}
    first_by_old_id: dict[str, str] = {}

    for node in nodes:
        old_id = str(node.get("id") or "")
        source_file = str(node.get("source_file") or "")
        if not old_id or not source_file:
            continue
        stem = make_id(_source_stem(source_file, root))
        digest = _source_hash(source_file, root)
        norm_old = normalize_id(old_id)
        prefix = make_id(stem, digest)
        if norm_old == prefix or norm_old.startswith(prefix + "_"):
            new_id = norm_old
        else:
            entity = norm_old
            if norm_old == stem:
                entity = ""
            elif norm_old.startswith(stem + "_"):
                entity = norm_old[len(stem) + 1:]
            new_id = make_id(stem, digest, entity)
        remap_by_source[(old_id, source_file)] = new_id
        first_by_old_id.setdefault(old_id, new_id)
        node["id"] = new_id

    def _remap_endpoint(value: Any, source_file: str) -> Any:
        if not isinstance(value, str):
            return value
        return remap_by_source.get((value, source_file)) or first_by_old_id.get(value, value)

    for edge in extraction.get("edges", []) or []:
        if not isinstance(edge, dict):
            continue
        source_file = str(edge.get("source_file") or "")
        edge["source"] = _remap_endpoint(edge.get("source"), source_file)
        edge["target"] = _remap_endpoint(edge.get("target"), source_file)

    for edge in extraction.get("links", []) or []:
        if not isinstance(edge, dict):
            continue
        source_file = str(edge.get("source_file") or "")
        edge["source"] = _remap_endpoint(edge.get("source"), source_file)
        edge["target"] = _remap_endpoint(edge.get("target"), source_file)

    for hyperedge in extraction.get("hyperedges", []) or []:
        if not isinstance(hyperedge, dict) or not isinstance(hyperedge.get("nodes"), list):
            continue
        source_file = str(hyperedge.get("source_file") or "")
        hyperedge["nodes"] = [_remap_endpoint(node_id, source_file) for node_id in hyperedge["nodes"]]

    for collision in collisions:
        new_ids = [
            remap_by_source[(collision["id"], source)]
            for source in collision["sources"]
            if (collision["id"], source) in remap_by_source
        ]
        for left, right in zip(new_ids, new_ids[1:]):
            source_file = next(
                source
                for source in collision["sources"]
                if remap_by_source.get((collision["id"], source)) == left
            )
            edge = {
                "source": left,
                "target": right,
                "relation": "semantically_similar_to",
                "confidence": "AMBIGUOUS",
                "confidence_score": 0.5,
                "source_file": source_file,
                "weight": 0.2,
                "context": f"pre-namespace semantic id collision on {collision['id']}",
            }
            if edge not in extraction.setdefault("edges", []):
                extraction["edges"].append(edge)

    flat_remap: dict[str, str] = {}
    for (old_id, _source), new_id in remap_by_source.items():
        flat_remap[old_id] = new_id
    return flat_remap, collisions


def record_source_attribution(audit: dict[str, Any], extraction: dict[str, Any]) -> None:
    violations = find_source_attribution_violations(extraction)
    audit["source_attribution_violations"] = violations
    for violation in violations:
        add_warning(
            audit,
            "missing_source_file",
            f"{violation['kind']} {violation['id']} has no source_file",
            details=violation,
        )


def record_collisions(
    audit: dict[str, Any],
    nodes: list[dict[str, Any]],
    extra_collisions: list[dict[str, Any]] | None = None,
) -> None:
    repaired_collisions = [dict(collision, repaired=True) for collision in (extra_collisions or [])]
    unresolved_collisions = find_node_id_collisions(nodes)
    collisions = [*repaired_collisions, *unresolved_collisions]
    audit["collisions"] = collisions
    for collision in repaired_collisions:
        add_warning(
            audit,
            "node_id_collision_repaired",
            f"node id {collision['id']} was namespaced across multiple source files",
            details=collision,
        )
    for collision in unresolved_collisions:
        add_warning(
            audit,
            "node_id_collision",
            f"node id {collision['id']} appears in multiple source files",
            details=collision,
        )


def strict_failures(audit: dict[str, Any]) -> list[dict[str, Any]]:
    failures = []
    for warning in audit.get("warnings", []):
        if warning.get("code") in STRICT_FATAL_CODES:
            failures.append(warning)
    return failures


def write_audit(audit: dict[str, Any], path: Path) -> None:
    finish_audit(audit)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")
