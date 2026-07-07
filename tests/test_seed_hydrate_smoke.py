from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

import graphify.__main__ as mainmod


def test_seed_hydrate_smoke_runs_seed_extract_in_temp_copy(
    monkeypatch, tmp_path, capsys
):
    project = tmp_path / "project"
    project.mkdir()
    (project / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    out = project / "graphify-out"
    out.mkdir()
    (out / ".graphify_root").write_text(str(project), encoding="utf-8")

    calls = []
    subprocess_kwargs = []
    real_run = subprocess.run

    def fake_run(args, **kwargs):
        if "cwd" not in kwargs:
            return real_run(args, **kwargs)
        calls.append(args)
        subprocess_kwargs.append(kwargs)
        audit_path = kwargs["cwd"] / "graphify-out" / "extraction-audit.json"
        audit_path.parent.mkdir(exist_ok=True)
        audit_path.write_text(
            json.dumps(
                {
                    "strict_failures": [],
                    "cache": {"semantic_cache_misses": 0},
                    "extraction": {"output_tokens": 0},
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(args, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(mainmod, "_check_skill_version", lambda _: None)
    monkeypatch.setenv("PYTHONPATH", "already-here")
    monkeypatch.setattr(
        mainmod.sys,
        "argv",
        ["graphify", "seed", "hydrate-smoke", str(project), "--json"],
    )

    mainmod.main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["semantic_cache_misses"] == 0
    assert payload["output_tokens"] == 0
    assert payload["strict_failures"] == []
    assert calls
    assert calls[0][:4] == [
        mainmod.sys.executable,
        "-m",
        "graphify",
        "extract",
    ]
    assert str(project) not in calls[0]
    assert "--seed" in calls[0]
    assert subprocess_kwargs[0]["cwd"] != project
    assert subprocess_kwargs[0]["cwd"].name == project.name

    child_env = subprocess_kwargs[0]["env"]
    repo_root = str(Path(mainmod.__file__).resolve().parent.parent)
    assert child_env["PYTHONPATH"].split(os.pathsep)[:2] == [
        repo_root,
        "already-here",
    ]


def test_seed_hydrate_smoke_exits_nonzero_when_seed_reextracts(
    monkeypatch, tmp_path, capsys
):
    project = tmp_path / "project"
    project.mkdir()
    (project / "README.md").write_text("# Notes\n", encoding="utf-8")
    real_run = subprocess.run

    def fake_run(args, **kwargs):
        if "cwd" not in kwargs:
            return real_run(args, **kwargs)
        audit_path = kwargs["cwd"] / "graphify-out" / "extraction-audit.json"
        audit_path.parent.mkdir(exist_ok=True)
        audit_path.write_text(
            json.dumps(
                {
                    "strict_failures": [
                        {"code": "semantic_cache_miss", "message": "miss"}
                    ],
                    "cache": {"semantic_cache_misses": 1},
                    "extraction": {"output_tokens": 5},
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="miss")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(mainmod, "_check_skill_version", lambda _: None)
    monkeypatch.setattr(
        mainmod.sys,
        "argv",
        ["graphify", "seed", "hydrate-smoke", str(project), "--json"],
    )

    with pytest.raises(SystemExit) as exc_info:
        mainmod.main()

    assert exc_info.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["returncode"] == 1
    assert payload["semantic_cache_misses"] == 1
    assert payload["output_tokens"] == 5
    assert payload["strict_failures"][0]["code"] == "semantic_cache_miss"
