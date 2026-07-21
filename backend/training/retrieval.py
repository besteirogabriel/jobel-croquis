from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter

import fitz

from backend.config import settings
from backend.corpus.discovery import sha256_file
from backend.engine.models import LocalExtraction

from .assets import render_pdf_images
from .dataset import load_or_build_manifest
from .models import DatasetCase

TOKEN_RE = re.compile(r"[A-ZÀ-Ü0-9]{3,}")
FORMAT_RE = re.compile(r"\bA([1-4])\b", re.IGNORECASE)


@dataclass(frozen=True)
class RetrievedReference:
    case_id: str
    score: float
    equipment_type: str
    equipment_code: str
    project_images: list[Path] = field(default_factory=list)
    target_images: list[Path] = field(default_factory=list)


def retrieve_reference_cases(
    pdf_path: Path,
    extraction: LocalExtraction,
    *,
    limit: int | None = None,
    telemetry=None,
) -> list[RetrievedReference]:
    if not settings.corpus_references_enabled:
        return []
    try:
        manifest = load_or_build_manifest()
    except (FileNotFoundError, OSError, ValueError):
        return []
    query_sha = sha256_file(pdf_path)
    query_tokens = _query_tokens(pdf_path, extraction)
    query_type_scores: dict[str, float] = {}
    for candidate in extraction.candidates[:12]:
        equipment_type = str(candidate.equipment_type)
        query_type_scores[equipment_type] = max(
            query_type_scores.get(equipment_type, 0.0), candidate.score
        )
    query_format = _format(pdf_path.name)
    candidates: list[tuple[float, DatasetCase]] = []
    for case in manifest.cases:
        if case.split not in settings.allowed_reference_splits:
            continue
        if case.project_sha256 == query_sha or not case.target_croqui_pdf:
            continue
        tokens = set(case.project_tokens)
        union = query_tokens | tokens
        score = (len(query_tokens & tokens) / len(union) * 8.0) if union else 0.0
        score += query_type_scores.get(case.equipment_type, 0.0) * 2.0
        if query_format and case.project_format == query_format:
            score += 1.0
        if case.corpus_status == "COMPLETE":
            score += 0.2
        candidates.append((score, case))
    candidates.sort(key=lambda value: (-value[0], value[1].case_id))
    result: list[RetrievedReference] = []
    maximum = max(0, limit if limit is not None else settings.corpus_reference_limit)
    render_started = perf_counter()
    selected: list[tuple[float, DatasetCase]] = []
    selected_ids: set[str] = set()
    selected_types: set[str] = set()
    for score, case in candidates:
        if case.equipment_type in selected_types:
            continue
        selected.append((score, case))
        selected_ids.add(case.case_id)
        selected_types.add(case.equipment_type)
        if len(selected) >= maximum:
            break
    for score, case in candidates:
        if len(selected) >= maximum:
            break
        if case.case_id in selected_ids:
            continue
        selected.append((score, case))
        selected_ids.add(case.case_id)

    for score, case in selected:
        project = Path(case.project_pdf)
        target = Path(case.target_croqui_pdf or "")
        if not project.is_file() or not target.is_file():
            continue
        result.append(
            RetrievedReference(
                case_id=case.case_id,
                score=round(score, 6),
                equipment_type=case.equipment_type,
                equipment_code=case.equipment_code,
                project_images=render_pdf_images(case.case_id, "project", project),
                target_images=render_pdf_images(case.case_id, "croqui", target),
            )
        )
    if telemetry is not None:
        telemetry("renderizacao_referencias", perf_counter() - render_started)
    return result


def _query_tokens(pdf_path: Path, extraction: LocalExtraction) -> set[str]:
    values = [pdf_path.stem, extraction.metadata.municipio, extraction.text]
    values.extend(f"{item.equipment_type} {item.number}" for item in extraction.candidates)
    if not extraction.text:
        try:
            with fitz.open(pdf_path) as document:
                values.extend(page.get_text("text") for page in document)
        except Exception:
            pass
    return set(TOKEN_RE.findall("\n".join(values).upper()))


def _format(name: str) -> str:
    match = FORMAT_RE.search(name)
    return f"A{match.group(1)}" if match else ""
