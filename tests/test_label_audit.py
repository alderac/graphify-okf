from __future__ import annotations

import json

import networkx as nx

import graphify.__main__ as mainmod
from graphify.export import to_json


def test_cluster_only_records_label_fallback_as_info(monkeypatch, tmp_path):
    out = tmp_path / "graphify-out"
    out.mkdir()
    G = nx.Graph()
    G.add_node("a", label="A", source_file="a.py", file_type="code")
    to_json(G, {0: ["a"]}, str(out / "graph.json"), force=True)
    (out / ".graphify_analysis.json").write_text(
        json.dumps({"communities": {"0": ["a"]}, "cohesion": {"0": 1.0}, "gods": [], "surprises": []}),
        encoding="utf-8",
    )
    monkeypatch.setattr(mainmod, "_check_skill_version", lambda _: None)
    monkeypatch.setattr(
        "graphify.llm.generate_community_labels",
        lambda *a, **k: ({0: "Community 0"}, "placeholder"),
    )
    monkeypatch.setattr(mainmod.sys, "argv", ["graphify", "cluster-only", str(tmp_path), "--no-viz"])

    mainmod.main()

    audit_path = tmp_path / "graphify-out" / "extraction-audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    warnings = [w for w in audit["warnings"] if w["code"] == "community_label_fallback"]
    assert warnings
    assert warnings[0]["severity"] == "info"
    assert warnings[0]["details"] == {"label_source": "placeholder"}
    assert audit["strict_failures"] == []
