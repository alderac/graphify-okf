from __future__ import annotations

import json

from graphify.audit import (
    add_warning,
    find_node_id_collisions,
    find_source_attribution_violations,
    new_extraction_audit,
    record_cache_status,
    strict_failures,
    write_audit,
)


def test_audit_records_cache_hits_misses_and_strict_failures(tmp_path):
    audit = new_extraction_audit(tmp_path, tmp_path, mode="seed", strict=True)
    record_cache_status(
        audit,
        semantic_inputs=["docs/a.md", "docs/b.md"],
        semantic_cache_hits=["docs/a.md"],
        semantic_cache_misses=["docs/b.md"],
    )

    failures = strict_failures(audit)

    assert audit["cache"]["semantic_inputs"] == 2
    assert audit["cache"]["semantic_cache_hits"] == 1
    assert audit["cache"]["semantic_cache_misses"] == 1
    assert audit["cache"]["miss_paths"] == ["docs/b.md"]
    assert any(f["code"] == "semantic_cache_miss" for f in failures)


def test_source_attribution_finds_nodes_edges_links_and_hyperedges():
    extraction = {
        "nodes": [
            {"id": "a", "label": "A", "file_type": "document", "source_file": "a.md"},
            {"id": "b", "label": "B", "file_type": "document"},
        ],
        "links": [
            {"source": "a", "target": "b", "relation": "references", "confidence": "EXTRACTED"},
        ],
        "hyperedges": [
            {"id": "h", "label": "H", "nodes": ["a", "b", "c"], "relation": "form"},
        ],
    }

    violations = find_source_attribution_violations(extraction)

    assert {"kind": "node", "index": 1, "id": "b"} in violations
    assert {"kind": "edge", "index": 0, "id": "a->b"} in violations
    assert {"kind": "hyperedge", "index": 0, "id": "h"} in violations


def test_collision_report_persists_old_and_new_sources():
    nodes = [
        {"id": "readme_booking_service", "source_file": "module-a/README.md", "label": "Booking Service"},
        {"id": "readme_booking_service", "source_file": "module-b/README.md", "label": "Booking Service"},
        {"id": "readme_booking_service", "source_file": "module-a/README.md", "label": "Booking Service copy"},
    ]

    collisions = find_node_id_collisions(nodes)

    assert collisions == [
        {
            "id": "readme_booking_service",
            "sources": ["module-a/README.md", "module-b/README.md"],
            "labels": ["Booking Service", "Booking Service copy"],
            "count": 3,
        }
    ]


def test_write_audit_is_machine_readable_json(tmp_path):
    audit = new_extraction_audit(tmp_path / "repo", tmp_path, mode="explore", strict=False)
    add_warning(audit, "community_label_fallback", "labels fell back", severity="info")
    out = tmp_path / "graphify-out" / "extraction-audit.json"

    write_audit(audit, out)

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["mode"] == "explore"
    assert payload["warnings"][0]["code"] == "community_label_fallback"
