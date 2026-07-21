from __future__ import annotations

import hashlib
import re
from collections import Counter
from pathlib import Path

from backend.config import BACKEND_DIR, settings

from .models import CorpusFile, CorpusRegistry, GoldenCase

ROOT_DIR = BACKEND_DIR.parent
PROJECT_TOKENS = ("PROJETO", "PROETO", "PROJ", " A1", " A2", " A3", " A4")
EQUIPMENT_RE = re.compile(r"\b(TR|FU|FC|RL|CF)\s*(\d{3,8})\b", re.IGNORECASE)


def resolve_corpus_path(path: str | Path | None = None) -> Path:
    raw = Path(path) if path is not None else settings.corpus_path
    if not raw.is_absolute():
        raw = ROOT_DIR / raw
    if not raw.is_dir():
        raise FileNotFoundError(f"Corpus oficial não encontrado: {raw}")
    return raw


def discover_corpus(path: str | Path | None = None) -> CorpusRegistry:
    root = resolve_corpus_path(path)
    cases = [
        discover_case(directory)
        for directory in sorted(root.iterdir())
        if directory.is_dir() and not directory.name.startswith(".")
    ]
    statuses = Counter(case.status for case in cases)
    types = Counter(case.equipment_type or "UNKNOWN" for case in cases)
    warnings = [f"{case.case_id}:{warning}" for case in cases for warning in case.warnings]
    return CorpusRegistry(
        source_path=str(root.resolve()),
        cases=cases,
        counts={"total": len(cases), **dict(statuses)},
        equipment_type_counts=dict(sorted(types.items())),
        warnings=warnings,
    )


def discover_case(directory: Path) -> GoldenCase:
    projects: list[CorpusFile] = []
    targets: list[CorpusFile] = []
    spreadsheets: list[CorpusFile] = []
    equipment_type = ""
    equipment_code = ""
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.name.startswith("."):
            continue
        kind = classify_file(path)
        item = CorpusFile(
            path=str(path.resolve()),
            name=path.name,
            kind=kind,
            size_bytes=path.stat().st_size,
            sha256=sha256_file(path),
        )
        if kind == "PROJECT_PDF":
            projects.append(item)
        elif kind == "TARGET_CROQUI_PDF":
            targets.append(item)
        elif kind == "TARGET_CROQUI_XLS":
            spreadsheets.append(item)
        if kind in {"TARGET_CROQUI_PDF", "TARGET_CROQUI_XLS"} and not equipment_code:
            match = EQUIPMENT_RE.search(path.stem)
            if match:
                equipment_type = "FC" if match.group(1).upper() == "CF" else match.group(1).upper()
                equipment_code = match.group(2)
    warnings: list[str] = []
    if len(spreadsheets) > 1:
        warnings.append("MULTIPLE_TARGET_XLS")
    if not projects:
        status = "MISSING_PROJECT"
        warnings.append("PROJECT_PDF_MISSING")
    elif not spreadsheets:
        status = "MISSING_XLS"
        warnings.append("TARGET_XLS_MISSING")
    elif not targets:
        status = "MISSING_TARGET_PDF"
        warnings.append("TARGET_PDF_MISSING")
    else:
        status = "COMPLETE"
    return GoldenCase(
        case_id=directory.name,
        directory=str(directory.resolve()),
        project_pdfs=projects,
        target_croqui_pdfs=targets,
        target_croqui_xls=spreadsheets[0] if spreadsheets else None,
        equipment_type=equipment_type,
        equipment_code=equipment_code,
        status=status,
        warnings=warnings,
    )


def classify_file(path: Path) -> str:
    suffix = path.suffix.lower()
    upper = f" {path.stem.upper()} "
    if suffix in {".xls", ".xlsx"}:
        return "TARGET_CROQUI_XLS"
    if suffix != ".pdf":
        return "OTHER"
    if any(token in upper for token in PROJECT_TOKENS):
        return "PROJECT_PDF"
    return "TARGET_CROQUI_PDF" if "CROQUI" in upper else "OTHER"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
