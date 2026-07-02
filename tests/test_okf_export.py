import json
from pathlib import Path

import yaml

from graphify.build import build_from_json
from graphify.export import to_okf_obsidian


FIXTURES = Path(__file__).parent / "fixtures"


def make_graph():
    return build_from_json(json.loads((FIXTURES / "extraction.json").read_text()))


def frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    return yaml.safe_load(text.split("---", 2)[1])


def test_to_okf_obsidian_writes_okf_concept_frontmatter(tmp_path):
    G = make_graph()
    communities = {
        0: ["n_transformer", "n_attention"],
        1: ["n_layernorm", "n_concept_attn"],
    }
    labels = {0: "Model Architecture", 1: "Paper Concepts"}

    n_notes = to_okf_obsidian(G, communities, str(tmp_path), community_labels=labels)

    assert n_notes == G.number_of_nodes() + len(communities)
    note = tmp_path / "concepts" / "Transformer.md"
    data = frontmatter(note)
    assert data["type"] == "Graphify Code Concept"
    assert data["title"] == "Transformer"
    assert data["description"] == "Graphify code node extracted from model.py."
    assert data["graphify_id"] == "n_transformer"
    assert data["graphify_file_type"] == "code"
    assert data["graphify_source_file"] == "model.py"
    assert data["graphify_source_location"] == "L1"
    assert data["graphify_community"] == "Model Architecture"
    assert "graphify/code" in data["graphify_tags"]
    assert data["tags"] == data["graphify_tags"]


def test_to_okf_obsidian_uses_standard_markdown_links(tmp_path):
    G = make_graph()
    communities = {0: list(G.nodes())}

    to_okf_obsidian(G, communities, str(tmp_path))

    text = (tmp_path / "concepts" / "Transformer.md").read_text(encoding="utf-8")
    assert "[[" not in text
    assert "[MultiHeadAttention](/concepts/MultiHeadAttention.md)" in text
    assert "[LayerNorm](/concepts/LayerNorm.md)" in text


def test_to_okf_obsidian_writes_community_notes_and_indexes(tmp_path):
    G = make_graph()
    communities = {
        0: ["n_transformer", "n_attention"],
        1: ["n_layernorm", "n_concept_attn"],
    }
    labels = {0: "Model Architecture", 1: "Paper Concepts"}

    to_okf_obsidian(G, communities, str(tmp_path), community_labels=labels)

    root = frontmatter(tmp_path / "index.md")
    assert root["okf_version"] == "0.1"
    assert (tmp_path / "concepts" / "index.md").exists()
    assert (tmp_path / "communities" / "index.md").exists()

    community = frontmatter(tmp_path / "communities" / "Model_Architecture.md")
    assert community["type"] == "Graphify Community"
    assert community["title"] == "Model Architecture"
    assert community["graphify_member_count"] == 2
    assert "graphify/community" in community["graphify_tags"]


def test_to_okf_obsidian_non_index_markdown_files_have_type(tmp_path):
    G = make_graph()
    communities = {
        0: ["n_transformer", "n_attention"],
        1: ["n_layernorm", "n_concept_attn"],
    }

    to_okf_obsidian(G, communities, str(tmp_path))

    for note in tmp_path.rglob("*.md"):
        if note.name == "index.md":
            continue
        assert "type" in frontmatter(note), note
