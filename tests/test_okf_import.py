import json
import subprocess
import sys

import pytest
from networkx.readwrite import json_graph

from graphify.okf import from_okf_bundle, write_graph


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _tokensave_bundle(tmp_path):
    _write(tmp_path / "index.md", '---\nokf_version: "0.2"\n---\n# Index\n')
    _write(
        tmp_path / "files" / "src" / "lib.rs.md",
        """---
type: Source File
title: src/lib.rs
description: Library source
tags: [tokensave/source-file]
tokensave_path: src/lib.rs
tokensave_content_hash: abc123
---
# src/lib.rs

# Dependencies

Files this file references, by edge kind:

* [src/util.rs](/files/src/util.rs.md) - calls ×3, uses ×1
""",
    )
    _write(
        tmp_path / "files" / "src" / "util.rs.md",
        """---
type: Source File
title: src/util.rs
tokensave_path: src/util.rs
---
# src/util.rs

# Dependents

* [src/lib.rs](/files/src/lib.rs.md) - calls ×3, uses ×1
""",
    )
    _write(
        tmp_path / "tokensave-export.md",
        """---
type: TokenSave Export
title: TokenSave index export
tokensave_schema_version: 21
---
# TokenSave index export
""",
    )
    return tmp_path


def test_from_okf_bundle_reads_tokensave_nodes_metadata_and_edges(tmp_path):
    graph = from_okf_bundle(_tokensave_bundle(tmp_path))

    assert set(graph) == {
        "files/src/lib.rs",
        "files/src/util.rs",
        "tokensave-export",
    }
    lib = graph.nodes["files/src/lib.rs"]
    assert lib["label"] == "src/lib.rs"
    assert lib["file_type"] == "code"
    assert lib["source_file"] == "src/lib.rs"
    assert lib["okf_frontmatter"]["tokensave_content_hash"] == "abc123"

    edge = graph.edges["files/src/lib.rs", "files/src/util.rs"]
    assert edge["relation"] == "calls"
    assert edge["tokensave_edge_kinds"] == {"calls": 3, "uses": 1}
    assert not graph.has_edge("files/src/util.rs", "files/src/lib.rs")


def test_from_okf_bundle_tolerates_broken_links_and_unknown_fields(tmp_path):
    bundle = _tokensave_bundle(tmp_path)
    concept = bundle / "custom.md"
    _write(
        concept,
        """---
type: Custom Concept
title: Custom
vendor_extension: {enabled: true}
---
# Custom

[missing](missing.md)
""",
    )

    graph = from_okf_bundle(bundle)
    assert graph.nodes["custom"]["okf_frontmatter"]["vendor_extension"] == {"enabled": True}
    assert graph.out_degree("custom") == 0


def test_write_graph_produces_loadable_node_link_json(tmp_path):
    graph = from_okf_bundle(_tokensave_bundle(tmp_path / "bundle"))
    output = write_graph(graph, tmp_path / "graphify-out" / "graph.json")
    data = json.loads(output.read_text(encoding="utf-8"))
    try:
        loaded = json_graph.node_link_graph(data, edges="links")
    except TypeError:
        loaded = json_graph.node_link_graph(data)
    assert set(loaded) == set(graph)
    assert loaded.has_edge("files/src/lib.rs", "files/src/util.rs")


def test_from_okf_bundle_rejects_cyclic_yaml_aliases(tmp_path):
    _write(
        tmp_path / "cycle.md",
        "---\ntype: Concept\ncycle: &cycle [*cycle]\n---\n# Cycle\n",
    )
    with pytest.raises(ValueError, match="cyclic YAML alias"):
        from_okf_bundle(tmp_path)


def test_import_okf_cli_writes_default_graph(tmp_path):
    bundle = _tokensave_bundle(tmp_path / "bundle")
    result = subprocess.run(
        [sys.executable, "-m", "graphify", "import-okf", str(bundle)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "3 nodes, 1 edges" in result.stdout
    assert (tmp_path / "graphify-out" / "graph.json").is_file()


def test_import_okf_cli_reports_invalid_bundle(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "graphify", "import-okf", str(tmp_path / "missing")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "error importing OKF bundle" in result.stderr
