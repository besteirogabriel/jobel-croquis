from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

import fitz

from backend.config import settings
from backend.corpus.discovery import discover_corpus
from backend.corpus.models import GoldenCase

from .models import DatasetCase, DatasetManifest, DatasetSplit, TrainingLabel
from .storage import label_path, manifest_path

SPLITS: tuple[DatasetSplit, ...] = ("train", "validation", "test")
DEFAULT_RATIOS: dict[DatasetSplit, float] = {"train": 0.70, "validation": 0.10, "test": 0.20}
TOKEN_RE = re.compile(r"[A-ZÀ-Ü0-9]{3,}")
FORMAT_RE = re.compile(r"\bA([1-4])\b", re.IGNORECASE)
MUNICIPALITY_RE = re.compile(r"MUNIC[IÍ]PIO\s*:?\s*([^\n\r]{2,80})", re.IGNORECASE)
STOP_WORDS = {"PARA", "COMO", "ESTA", "ESTE", "PROJETO", "FOLHA", "ESCALA", "DATA", "OBRA"}


def build_manifest(
    corpus_path: str | Path | None = None,
    *,
    output: str | Path | None = None,
    seed: str | None = None,
) -> DatasetManifest:
    registry = discover_corpus(corpus_path)
    seed = seed or settings.training_split_seed
    eligible = [case for case in registry.cases if case.project_pdfs and case.target_croqui_xls]
    assignments = _assign_splits(eligible, DEFAULT_RATIOS, seed)
    cases = [_dataset_case(case, assignments[case.case_id]) for case in eligible]
    manifest = DatasetManifest(
        source_path=registry.source_path,
        corpus_fingerprint=_fingerprint(cases),
        seed=seed,
        ratios=DEFAULT_RATIOS,
        cases=cases,
        counts={split: sum(case.split == split for case in cases) for split in SPLITS},
        equipment_type_counts=dict(Counter(case.equipment_type or "UNKNOWN" for case in cases)),
        split_equipment_counts={
            split: dict(Counter(case.equipment_type or "UNKNOWN" for case in cases if case.split == split))
            for split in SPLITS
        },
        warnings=sorted(set(registry.warnings + _split_warnings(cases))),
    )
    destination = Path(output) if output else manifest_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest


def load_manifest(path: str | Path | None = None) -> DatasetManifest:
    source = Path(path) if path else manifest_path()
    manifest = DatasetManifest.model_validate_json(source.read_text(encoding="utf-8"))
    for case in manifest.cases:
        path = label_path(case.case_id)
        if not path.is_file():
            continue
        try:
            label = TrainingLabel.model_validate_json(path.read_text(encoding="utf-8"))
            case.label_status = label.status
            case.label_path = str(path.resolve())
        except Exception:
            case.warnings.append("TRAINING_LABEL_INVALID")
    return manifest


def load_or_build_manifest() -> DatasetManifest:
    return load_manifest() if manifest_path().is_file() else build_manifest()


def _dataset_case(case: GoldenCase, split: DatasetSplit) -> DatasetCase:
    project = case.project_pdfs[0]
    target_pdf = case.target_croqui_pdfs[0] if case.target_croqui_pdfs else None
    assert case.target_croqui_xls is not None
    text = ""
    warnings = list(case.warnings)
    try:
        with fitz.open(project.path) as document:
            text = "\n".join(page.get_text("text") for page in document)[:250000]
    except Exception as exc:
        warnings.append(f"PROJECT_PDF_INSPECTION_FAILED:{type(exc).__name__}")
    tokens = [token for token in TOKEN_RE.findall(text.upper()) if token not in STOP_WORDS]
    ranked = sorted(Counter(tokens), key=lambda token: (-Counter(tokens)[token], token))[:320]
    municipality = MUNICIPALITY_RE.search(text)
    project_format = FORMAT_RE.search(project.name)
    return DatasetCase(
        case_id=case.case_id,
        group_key=f"{case.equipment_type or 'UNKNOWN'}:{case.equipment_code or case.case_id}",
        split=split,
        corpus_status=case.status,
        equipment_type=case.equipment_type,
        equipment_code=case.equipment_code,
        project_pdf=project.path,
        project_sha256=project.sha256,
        target_croqui_pdf=target_pdf.path if target_pdf else None,
        target_croqui_sha256=target_pdf.sha256 if target_pdf else None,
        target_croqui_xls=case.target_croqui_xls.path,
        target_croqui_xls_sha256=case.target_croqui_xls.sha256,
        project_format=f"A{project_format.group(1)}" if project_format else "",
        project_tokens=ranked,
        municipality_hint=municipality.group(1).strip()[:80] if municipality else "",
        warnings=sorted(set(warnings)),
    )


def _assign_splits(
    cases: list[GoldenCase], ratios: dict[DatasetSplit, float], seed: str
) -> dict[str, DatasetSplit]:
    grouped: dict[str, dict[str, list[GoldenCase]]] = defaultdict(lambda: defaultdict(list))
    for case in cases:
        equipment_type = case.equipment_type or "UNKNOWN"
        key = f"{equipment_type}:{case.equipment_code or case.case_id}"
        grouped[equipment_type][key].append(case)
    result: dict[str, DatasetSplit] = {}
    for equipment_type, by_group in sorted(grouped.items()):
        groups = sorted(
            by_group.items(), key=lambda item: (-len(item[1]), _stable(seed, equipment_type, item[0]))
        )
        total = sum(len(items) for _, items in groups)
        targets = _split_targets(total, ratios)
        assigned = {split: 0 for split in SPLITS}
        for group_key, group_cases in groups:
            size = len(group_cases)
            split = min(
                SPLITS,
                key=lambda candidate: (
                    sum(
                        abs(assigned[item] + (size if item == candidate else 0) - targets[item])
                        for item in SPLITS
                    ),
                    _stable(seed, group_key, candidate),
                ),
            )
            assigned[split] += size
            result.update({case.case_id: split for case in group_cases})
    return result


def _split_targets(total: int, ratios: dict[DatasetSplit, float]) -> dict[DatasetSplit, int]:
    raw = {split: total * ratios[split] for split in SPLITS}
    result = {split: math.floor(raw[split]) for split in SPLITS}
    remainder = total - sum(result.values())
    for split in sorted(SPLITS, key=lambda item: raw[item] - result[item], reverse=True)[:remainder]:
        result[split] += 1
    return result


def _fingerprint(cases: list[DatasetCase]) -> str:
    digest = hashlib.sha256()
    for case in sorted(cases, key=lambda item: item.case_id):
        digest.update(
            f"{case.case_id}:{case.project_sha256}:{case.target_croqui_xls_sha256}:"
            f"{case.target_croqui_sha256 or ''}:{case.split}".encode()
        )
    return digest.hexdigest()


def _stable(*values: str) -> str:
    return hashlib.sha256("\x1f".join(values).encode()).hexdigest()


def _split_warnings(cases: list[DatasetCase]) -> list[str]:
    by_group: dict[str, set[str]] = defaultdict(set)
    for case in cases:
        by_group[case.group_key].add(case.split)
    return [f"SPLIT_GROUP_LEAKAGE:{key}" for key, splits in by_group.items() if len(splits) > 1]
