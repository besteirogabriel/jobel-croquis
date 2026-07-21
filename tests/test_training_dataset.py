from pathlib import Path

from backend.corpus.discovery import discover_corpus
from backend.training.dataset import build_manifest


def _case(root: Path, case_id: str, equipment: str) -> None:
    folder = root / case_id
    folder.mkdir()
    (folder / f"{case_id} PROJETO A3.pdf").write_bytes(b"not-a-real-pdf")
    (folder / f"croqui {equipment}.pdf").write_bytes(b"not-a-real-pdf")
    (folder / f"croqui {equipment}.xls").write_bytes(b"xls")


def test_manifest_keeps_same_equipment_group_in_one_split(tmp_path: Path):
    corpus = tmp_path / "CROQUI IA"
    corpus.mkdir()
    _case(corpus, "001", "FU 900001")
    _case(corpus, "002", "FU 900001")
    _case(corpus, "003", "TR 900002")
    manifest = build_manifest(corpus, output=tmp_path / "manifest.json", seed="test")
    groups: dict[str, set[str]] = {}
    for case in manifest.cases:
        groups.setdefault(case.group_key, set()).add(case.split)
    assert all(len(splits) == 1 for splits in groups.values())
    assert manifest.corpus_fingerprint


def test_corpus_inventory_classifies_pairs(tmp_path: Path):
    corpus = tmp_path / "CROQUI IA"
    corpus.mkdir()
    _case(corpus, "001", "RL 900003")
    registry = discover_corpus(corpus)
    assert registry.counts["total"] == 1
    assert registry.counts["COMPLETE"] == 1
    assert registry.cases[0].equipment_type == "RL"
    assert registry.cases[0].equipment_code == "900003"
