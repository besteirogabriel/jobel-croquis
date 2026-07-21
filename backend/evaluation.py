from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from time import perf_counter
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from backend.config import settings
from backend.corpus.discovery import discover_corpus
from backend.engine.ai_fallback import CodexPlanFallback
from backend.engine.decision import decide_local
from backend.engine.excel_native import _top_name, _workbook_parts
from backend.engine.extraction import extract_project
from backend.engine.service import CroquiEngine
from backend.training.dataset import load_or_build_manifest

TARGET_SYMBOLS = {
    "Group 184": "POLE_EXISTING",
    "Oval 148": "POLE_EXISTING",
    "Group 24667": "POLE_EXISTING",
    "Group 156": "POLE_NEW",
    "AutoShape 238": "TR",
    "AutoShape 243": "TR_PRIVATE",
    "Group 729": "FU_LOAD_BREAK",
    "Group 718": "FU_NO_LOAD_BREAK",
    "Group 302": "KNIFE_NO_LOAD_BREAK",
    "Group 319": "KNIFE_LOAD_BREAK",
    "Group 321": "KNIFE_TRIPOLAR_NO_LOAD_BREAK",
    "Group 332": "KNIFE_TRIPOLAR_LOAD_BREAK",
    "Group 72": "GROUND_BT",
    "Group 80": "GROUND_AT",
    "Group 225": "SECTION_PRIMARY",
    "Group 226": "SECTION_SECONDARY",
    "Text Box 245": "RL",
    "Text Box 247": "SC",
    "Group 254": "CAPACITOR",
    "Group 260": "RG",
    "Text Box 261": "OIL_UNIPOLAR",
    "Text Box 263": "OIL_TRIPOLAR",
    "Rectangle 334": "WORK_ZONE_RECT",
    "Oval 26855": "WORK_ZONE_OVAL",
    "Line 429": "LINE_SECONDARY",
    "Line 430": "LINE_PRIMARY",
    "Line 431": "LINE_PROJECTED",
}


def run_local_blind(output_dir: Path, *, ocr: bool = True) -> dict:
    """Gera previsões sem abrir qualquer croqui homologado."""
    registry = discover_corpus()
    predictions = output_dir / "predictions"
    if predictions.exists() and any(predictions.iterdir()):
        raise FileExistsError(f"execução cega já contém previsões: {predictions}")
    predictions.mkdir(parents=True, exist_ok=True)
    failures: list[dict[str, str]] = []
    for case in registry.cases:
        if not case.project_pdfs:
            failures.append({"case_id": case.case_id, "error": "PROJECT_MISSING"})
            continue
        try:
            project = Path(case.project_pdfs[0].path)
            extraction = extract_project(
                project,
                ocr_enabled=ocr,
                tesseract_bin=settings.tesseract_bin,
                ocr_language=settings.ocr_language,
                ocr_dpi=settings.ocr_dpi,
            )
            plan = decide_local(
                extraction,
                threshold=settings.local_auto_threshold,
                minimum_gap=settings.local_min_gap,
            )
            payload = {
                "case_id": case.case_id,
                "project_sha256": case.project_pdfs[0].sha256,
                "plan": plan.model_dump(mode="json"),
                "observed_identifiers": extraction.identifiers,
                "observed_candidates": [
                    {
                        "equipment_type": str(item.equipment_type),
                        "number": item.number,
                    }
                    for item in extraction.candidates
                ],
            }
            (predictions / f"{case.case_id}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as exc:
            failures.append({"case_id": case.case_id, "error": type(exc).__name__})
    summary = {"cases": len(registry.cases), "predictions": len(list(predictions.glob("*.json"))), "failures": failures}
    (output_dir / "blind-complete.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def run_runtime_blind(
    output_dir: Path,
    *,
    workers: int = 2,
    case_id: str | None = None,
    references: bool = True,
) -> dict:
    """Executa a análise visual sem expor o gabarito do caso atual."""
    registry = discover_corpus()
    selected_cases = [
        case for case in registry.cases if case_id is None or case.case_id == case_id
    ]
    if case_id is not None and not selected_cases:
        raise KeyError(f"caso não encontrado: {case_id}")
    predictions = output_dir / "predictions"
    if predictions.exists() and any(predictions.iterdir()):
        raise FileExistsError(f"execução cega já contém previsões: {predictions}")
    predictions.mkdir(parents=True, exist_ok=True)
    work = output_dir / "work"
    work.mkdir(parents=True, exist_ok=True)

    def evaluate(case) -> dict:
        case_started = perf_counter()
        steps: list[dict[str, float | str]] = []

        def record(name: str, elapsed: float) -> None:
            steps.append({"step": name, "seconds": round(max(elapsed, 0), 3)})

        if not case.project_pdfs:
            return {
                "case_id": case.case_id,
                "error": "PROJECT_MISSING",
                "elapsed_seconds": round(perf_counter() - case_started, 3),
            }
        try:
            case_dir = work / case.case_id
            case_dir.mkdir(parents=True, exist_ok=True)
            project = case_dir / "project.pdf"
            shutil.copy2(case.project_pdfs[0].path, project)
            extraction = extract_project(
                project,
                ocr_enabled=settings.ocr_enabled,
                tesseract_bin=settings.tesseract_bin,
                ocr_language=settings.ocr_language,
                ocr_dpi=settings.ocr_dpi,
                telemetry=record,
            )
            fallback = CodexPlanFallback(
                binary=settings.codex_bin,
                model=settings.codex_model,
                reasoning_effort=settings.codex_reasoning_effort,
                timeout=settings.codex_timeout_seconds,
                max_project_pages=settings.codex_max_project_pages,
            )
            plan = fallback.propose(project, extraction, telemetry=record)
            if plan is None:
                raise RuntimeError("EMPTY_PLAN")
            CroquiEngine._normalize_main_equipment(plan)
            CroquiEngine._normalize_scene_primitives(plan, extraction)
            payload = {
                "case_id": case.case_id,
                "project_sha256": case.project_pdfs[0].sha256,
                "plan": plan.model_dump(mode="json"),
                "observed_identifiers": extraction.identifiers,
                "observed_candidates": [
                    {
                        "equipment_type": str(item.equipment_type),
                        "number": item.number,
                    }
                    for item in extraction.candidates
                ],
                "timings": steps,
            }
            (predictions / f"{case.case_id}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return {
                "case_id": case.case_id,
                "elapsed_seconds": round(perf_counter() - case_started, 3),
            }
        except Exception as exc:
            return {
                "case_id": case.case_id,
                "error": type(exc).__name__,
                "elapsed_seconds": round(perf_counter() - case_started, 3),
            }

    references_enabled = settings.corpus_references_enabled
    settings.corpus_references_enabled = references
    outcomes: list[dict] = []
    try:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
            futures = [executor.submit(evaluate, case) for case in selected_cases]
            for future in as_completed(futures):
                outcomes.append(future.result())
    finally:
        settings.corpus_references_enabled = references_enabled
    failures = [item for item in outcomes if item.get("error")]
    elapsed_values = [float(item["elapsed_seconds"]) for item in outcomes]
    summary = {
        "cases": len(selected_cases),
        "predictions": len(list(predictions.glob("*.json"))),
        "failures": sorted(failures, key=lambda item: item["case_id"]),
        "references_enabled": references,
        "elapsed_seconds": {
            "mean": round(sum(elapsed_values) / max(len(elapsed_values), 1), 3),
            "maximum": round(max(elapsed_values, default=0), 3),
        },
        "cases_timing": sorted(outcomes, key=lambda item: item["case_id"]),
    }
    (output_dir / "blind-complete.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def compare_blind(output_dir: Path) -> dict:
    marker = output_dir / "blind-complete.json"
    if not marker.is_file():
        raise RuntimeError("comparação recusada: execução cega ainda não foi concluída")
    blind_summary = json.loads(marker.read_text(encoding="utf-8"))
    registry = discover_corpus()
    split_by_case = {case.case_id: case.split for case in load_or_build_manifest().cases}
    rows: list[dict] = []
    for case in registry.cases:
        prediction_path = output_dir / "predictions" / f"{case.case_id}.json"
        if not prediction_path.is_file() or case.target_croqui_xls is None:
            continue
        prediction_payload = json.loads(prediction_path.read_text(encoding="utf-8"))
        prediction = prediction_payload["plan"]
        target_xlsx = _target_xlsx(Path(case.target_croqui_xls.path), output_dir / "targets-xlsx" / case.case_id)
        expected_symbols = _target_symbol_counts(target_xlsx)
        actual_symbols = _prediction_symbol_counts(prediction)
        main = prediction.get("main_equipment") or {}
        expected_main = f"{case.equipment_type} {case.equipment_code}".strip()
        actual_main = f"{main.get('equipment_type', '')} {main.get('number', '')}".strip()
        keys = sorted(set(expected_symbols) | set(actual_symbols))
        intersection = sum(
            min(expected_symbols.get(key, 0), actual_symbols.get(key, 0)) for key in keys
        )
        predicted_total = sum(actual_symbols.values())
        expected_total = sum(expected_symbols.values())
        precision = intersection / predicted_total if predicted_total else 0.0
        recall = intersection / expected_total if expected_total else 0.0
        rows.append(
            {
                "case_id": case.case_id,
                "split": split_by_case.get(case.case_id, "unknown"),
                "main_exact": actual_main == expected_main,
                "expected_main": expected_main,
                "actual_main": actual_main,
                "expected_identifier_observed": case.equipment_code
                in prediction_payload.get("observed_identifiers", []),
                "expected_typed_candidate_observed": {
                    "equipment_type": case.equipment_type,
                    "number": case.equipment_code,
                }
                in prediction_payload.get("observed_candidates", []),
                "symbol_precision": round(precision, 4),
                "symbol_recall": round(recall, 4),
                "expected_symbols": expected_symbols,
                "actual_symbols": actual_symbols,
            }
        )
    summary = {
        "cases_compared": len(rows),
        "main_exact": sum(row["main_exact"] for row in rows),
        "main_accuracy": round(sum(row["main_exact"] for row in rows) / max(len(rows), 1), 4),
        "mean_symbol_precision": round(sum(row["symbol_precision"] for row in rows) / max(len(rows), 1), 4),
        "mean_symbol_recall": round(sum(row["symbol_recall"] for row in rows) / max(len(rows), 1), 4),
        "expected_identifier_observed": sum(
            row["expected_identifier_observed"] for row in rows
        ),
        "expected_typed_candidate_observed": sum(
            row["expected_typed_candidate_observed"] for row in rows
        ),
    }
    summary["by_split"] = {
        split: _summarize_rows([row for row in rows if row["split"] == split])
        for split in ("train", "validation", "test")
    }
    test_summary = summary["by_split"]["test"]
    checks = {
        "all_cases_predicted": len(rows) == len(registry.cases) and not blind_summary["failures"],
        "main_accuracy_at_least_90pct": summary["main_accuracy"] >= 0.90,
        "test_main_accuracy_at_least_90pct": test_summary["main_accuracy"] >= 0.90,
        "symbol_precision_at_least_80pct": summary["mean_symbol_precision"] >= 0.80,
        "symbol_recall_at_least_75pct": summary["mean_symbol_recall"] >= 0.75,
        "test_symbol_recall_at_least_75pct": test_summary["mean_symbol_recall"] >= 0.75,
    }
    summary["readiness"] = {"ready": all(checks.values()), "checks": checks}
    (output_dir / "comparison.json").write_text(
        json.dumps({"summary": summary, "cases": rows}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def _summarize_rows(rows: list[dict]) -> dict:
    return {
        "cases": len(rows),
        "main_accuracy": round(sum(row["main_exact"] for row in rows) / max(len(rows), 1), 4),
        "mean_symbol_precision": round(
            sum(row["symbol_precision"] for row in rows) / max(len(rows), 1), 4
        ),
        "mean_symbol_recall": round(
            sum(row["symbol_recall"] for row in rows) / max(len(rows), 1), 4
        ),
    }


def _target_xlsx(source: Path, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"{source.stem}.xlsx"
    if target.is_file():
        return target
    profile = Path(tempfile.mkdtemp(prefix="jobel-eval-lo-"))
    completed = subprocess.run(
        [
            settings.libreoffice_bin,
            f"-env:UserInstallation={profile.as_uri()}",
            "--headless",
            "--convert-to",
            "xlsx:Calc MS Excel 2007 XML",
            "--outdir",
            str(output_dir),
            str(source),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if completed.returncode != 0 or not target.is_file():
        raise RuntimeError(f"não foi possível converter o gabarito {source.name}")
    return target


def _target_symbol_counts(path: Path) -> dict[str, int]:
    counts: Counter[str] = Counter()
    with ZipFile(path) as archive:
        parts = _workbook_parts(archive)
        root = ET.fromstring(archive.read(parts.croqui_drawing))
    for anchor in root:
        kind = TARGET_SYMBOLS.get(_top_name(anchor))
        if kind:
            counts[kind] += 1
    return dict(sorted(counts.items()))


def _prediction_symbol_counts(plan: dict) -> dict[str, int]:
    counts: Counter[str] = Counter()
    counts["POLE_EXISTING"] += len(plan.get("poles", []))
    counts["WORK_ZONE_RECT"] += len(plan.get("work_zones", []))
    for item in plan.get("equipment", []):
        equipment_type = str(item.get("equipment_type", ""))
        if equipment_type == "FU":
            counts["FU_LOAD_BREAK"] += 1
        elif equipment_type == "FC":
            counts["KNIFE_LOAD_BREAK"] += 1
        elif equipment_type == "OL":
            counts["OIL_UNIPOLAR"] += 1
        elif equipment_type:
            counts[equipment_type] += 1
    symbol_names = {
        "POLE_NEW": "POLE_NEW",
        "TR_PRIVATE": "TR_PRIVATE",
        "FUSE_NO_LOAD_BREAK": "FU_NO_LOAD_BREAK",
        "KNIFE_NO_LOAD_BREAK": "KNIFE_NO_LOAD_BREAK",
    }
    for item in plan.get("symbols", []):
        value = str(item.get("symbol_type", ""))
        if value:
            counts[symbol_names.get(value, value)] += 1
    line_types = {
        "secondary": "LINE_SECONDARY",
        "primary": "LINE_PRIMARY",
        "projected": "LINE_PROJECTED",
        "work": "LINE_PROJECTED",
    }
    for segment in plan.get("segments", []):
        counts[line_types.get(segment.get("style"), "LINE_PRIMARY")] += 1
    return dict(sorted((key, value) for key, value in counts.items() if value))
