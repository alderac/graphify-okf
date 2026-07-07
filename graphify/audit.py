from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
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


def record_collisions(audit: dict[str, Any], nodes: list[dict[str, Any]]) -> None:
    collisions = find_node_id_collisions(nodes)
    audit["collisions"] = collisions
    for collision in collisions:
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
