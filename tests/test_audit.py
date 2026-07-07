from __future__ import annotations

import json

from graphify.audit import (
    add_warning,
    backfill_single_file_source,
    find_node_id_collisions,
    find_source_attribution_violations,
    namespace_semantic_node_ids,
    new_extraction_audit,
    record_cache_status,
    record_collisions,
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


def test_source_attribution_checks_edges_and_links_when_both_present():
    extraction = {
        "nodes": [{"id": "a", "label": "A", "source_file": "a.md"}],
        "edges": [{"source": "a", "target": "b"}],
        "links": [{"source": "a", "target": "c"}],
    }

    violations = find_source_attribution_violations(extraction)

    assert {"kind": "edge", "index": 0, "id": "a->b"} in violations
    assert {"kind": "edge", "index": 0, "id": "a->c"} in violations


def test_backfill_single_file_source_fills_all_artifact_kinds():
    extraction = {
        "nodes": [{"id": "a", "label": "A", "file_type": "document"}],
        "edges": [{"source": "a", "target": "a", "relation": "references", "confidence": "EXTRACTED"}],
        "links": [{"source": "a", "target": "b", "relation": "mentions", "confidence": "EXTRACTED"}],
        "hyperedges": [{"id": "h", "label": "H", "nodes": ["a", "b", "c"], "relation": "form"}],
    }

    backfill_single_file_source(extraction, "docs/one.md")

    assert extraction["nodes"][0]["source_file"] == "docs/one.md"
    assert extraction["edges"][0]["source_file"] == "docs/one.md"
    assert extraction["links"][0]["source_file"] == "docs/one.md"
    assert extraction["hyperedges"][0]["source_file"] == "docs/one.md"


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


def test_namespace_semantic_node_ids_uses_source_path_and_content_hash(tmp_path):
    a = tmp_path / "module-a" / "README.md"
    b = tmp_path / "module-b" / "README.md"
    a.parent.mkdir()
    b.parent.mkdir()
    a.write_text("# Booking\n", encoding="utf-8")
    b.write_text("# Booking\n", encoding="utf-8")
    extraction = {
        "nodes": [
            {"id": "readme_booking_service", "label": "Booking Service", "file_type": "concept", "source_file": str(a)},
            {"id": "readme_booking_service", "label": "Booking Service", "file_type": "concept", "source_file": str(b)},
        ],
        "edges": [
            {
                "source": "readme_booking_service",
                "target": "readme_booking_service",
                "relation": "references",
                "confidence": "EXTRACTED",
                "source_file": str(a),
            }
        ],
        "hyperedges": [],
    }

    remap, collisions = namespace_semantic_node_ids(extraction, tmp_path)

    ids = [n["id"] for n in extraction["nodes"]]
    assert len(set(ids)) == 2
    assert all("readme_booking_service" in node_id for node_id in ids)
    assert all("module_" in node_id for node_id in ids)
    assert remap["readme_booking_service"] in ids
    assert collisions[0]["id"] == "readme_booking_service"
    assert any(e["relation"] == "semantically_similar_to" for e in extraction["edges"])


def test_namespace_semantic_node_ids_is_idempotent_for_cached_results(tmp_path):
    source = tmp_path / "docs" / "README.md"
    source.parent.mkdir()
    source.write_text("# Cached\n", encoding="utf-8")
    extraction = {
        "nodes": [
            {"id": "readme_cached_concept", "label": "Cached Concept", "source_file": str(source)},
        ],
        "edges": [
            {
                "source": "readme_cached_concept",
                "target": "readme_cached_concept",
                "relation": "references",
                "source_file": str(source),
            }
        ],
        "hyperedges": [{"id": "h", "nodes": ["readme_cached_concept"], "source_file": str(source)}],
    }

    namespace_semantic_node_ids(extraction, tmp_path)
    once = json.loads(json.dumps(extraction))
    namespace_semantic_node_ids(extraction, tmp_path)

    assert extraction == once


def test_record_collisions_preserves_repaired_pre_namespace_reports(tmp_path):
    audit = new_extraction_audit(tmp_path, tmp_path, mode="seed", strict=True)
    nodes = [
        {"id": "docs_a_readme_12345678_booking", "source_file": "docs/a/README.md"},
        {"id": "docs_b_readme_87654321_booking", "source_file": "docs/b/README.md"},
    ]
    pre_namespace = [
        {
            "id": "readme_booking",
            "sources": ["docs/a/README.md", "docs/b/README.md"],
            "labels": ["Booking"],
            "count": 2,
        }
    ]

    record_collisions(audit, nodes, pre_namespace)

    assert audit["collisions"] == [dict(pre_namespace[0], repaired=True)]
    assert any(w["code"] == "node_id_collision_repaired" for w in audit["warnings"])
    assert strict_failures(audit) == []


def test_record_collisions_fails_unresolved_node_id_collisions(tmp_path):
    audit = new_extraction_audit(tmp_path, tmp_path, mode="seed", strict=True)
    nodes = [
        {"id": "readme_booking", "source_file": "docs/a/README.md"},
        {"id": "readme_booking", "source_file": "docs/b/README.md"},
    ]

    record_collisions(audit, nodes)

    assert any(c["id"] == "readme_booking" for c in audit["collisions"])
    assert any(f["code"] == "node_id_collision" for f in strict_failures(audit))


def test_write_audit_is_machine_readable_json(tmp_path):
    audit = new_extraction_audit(tmp_path / "repo", tmp_path, mode="explore", strict=False)
    add_warning(audit, "community_label_fallback", "labels fell back", severity="info")
    out = tmp_path / "graphify-out" / "extraction-audit.json"

    write_audit(audit, out)

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["mode"] == "explore"
    assert payload["warnings"][0]["code"] == "community_label_fallback"
