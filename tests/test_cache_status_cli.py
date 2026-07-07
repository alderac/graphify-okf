from __future__ import annotations

import json

import pytest

import graphify.__main__ as mainmod
from graphify.cache import save_semantic_cache


def test_cache_status_json_reports_semantic_misses(monkeypatch, tmp_path, capsys):
    (tmp_path / "README.md").write_text("# Readme\n")
    (tmp_path / "code.py").write_text("def f():\n    return 1\n")
    monkeypatch.setattr(mainmod, "_check_skill_version", lambda _: None)
    monkeypatch.setattr(
        mainmod.sys,
        "argv",
        ["graphify", "cache", "status", str(tmp_path), "--json"],
    )

    mainmod.main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["semantic_inputs"] == 1
    assert payload["semantic_cache_hits"] == 0
    assert payload["semantic_cache_misses"] == 1
    assert payload["miss_paths"] == [str(tmp_path / "README.md")]


def test_cache_status_json_reports_semantic_hits(monkeypatch, tmp_path, capsys):
    readme = tmp_path / "README.md"
    readme.write_text("# Readme\n")
    save_semantic_cache(
        [
            {
                "id": "readme",
                "label": "Readme",
                "type": "document",
                "source_file": str(readme),
            }
        ],
        [],
        [],
        root=tmp_path,
    )
    monkeypatch.setattr(mainmod, "_check_skill_version", lambda _: None)
    monkeypatch.setattr(
        mainmod.sys,
        "argv",
        ["graphify", "cache", "status", str(tmp_path), "--json"],
    )

    mainmod.main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["semantic_inputs"] == 1
    assert payload["semantic_cache_hits"] == 1
    assert payload["semantic_cache_misses"] == 0
    assert payload["hit_paths"] == [str(readme)]
    assert payload["miss_paths"] == []


def test_cache_status_usage_error(monkeypatch, capsys):
    monkeypatch.setattr(mainmod, "_check_skill_version", lambda _: None)
    monkeypatch.setattr(mainmod.sys, "argv", ["graphify", "cache", "unknown"])

    with pytest.raises(SystemExit) as exc_info:
        mainmod.main()

    assert exc_info.value.code == 1
    assert "Usage: graphify cache status" in capsys.readouterr().err
