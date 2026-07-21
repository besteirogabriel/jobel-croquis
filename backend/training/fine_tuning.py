from __future__ import annotations

import json
from pathlib import Path

from openai import OpenAI

from backend.config import settings
from backend.engine.ai_fallback import AIConfigurationError

from .assets import image_data_url, render_pdf_images
from .dataset import load_manifest
from .models import FineTuningBuildReport, TrainingLabel
from .storage import datasets_dir, jobs_dir, label_path

SYSTEM_PROMPT = (
    "Analise o projeto elétrico e devolva somente CroquiPlan. O backend materializa os objetos "
    "oficiais da aba Simbologia."
)


def build_fine_tuning_files(output_dir: Path | None = None) -> FineTuningBuildReport:
    manifest = load_manifest()
    output_dir = output_dir or datasets_dir() / manifest.corpus_fingerprint[:16]
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "train": output_dir / "train.jsonl",
        "validation": output_dir / "validation.jsonl",
        "test": output_dir / "test-evals.jsonl",
    }
    handles = {split: path.open("w", encoding="utf-8") for split, path in paths.items()}
    counts = {split: 0 for split in paths}
    skipped: list[dict[str, str]] = []
    try:
        for case in manifest.cases:
            label_file = label_path(case.case_id)
            if not label_file.is_file():
                skipped.append({"case_id": case.case_id, "reason": "LABEL_MISSING"})
                continue
            label = TrainingLabel.model_validate_json(label_file.read_text(encoding="utf-8"))
            if label.status != "approved":
                skipped.append({"case_id": case.case_id, "reason": "LABEL_NOT_APPROVED"})
                continue
            images = render_pdf_images(
                case.case_id,
                "fine_tuning_project",
                Path(case.project_pdf),
                max_pages=settings.fine_tuning_max_project_pages,
            )
            record = _record(case.case_id, label, images)
            handles[case.split].write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            counts[case.split] += 1
    finally:
        for handle in handles.values():
            handle.close()
    report = FineTuningBuildReport(
        manifest_fingerprint=manifest.corpus_fingerprint,
        training_file=str(paths["train"]),
        validation_file=str(paths["validation"]),
        evaluation_file=str(paths["test"]),
        counts=counts,
        skipped=skipped,
    )
    (output_dir / "build-report.json").write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report


def submit_job(training_file: Path, validation_file: Path | None = None, *, client=None) -> dict:
    if not settings.fine_tuning_enabled:
        raise AIConfigurationError("Envio bloqueado. Defina FINE_TUNING_ENABLED=true conscientemente.")
    if not settings.fine_tuning_base_model:
        raise AIConfigurationError("FINE_TUNING_BASE_MODEL não configurado.")
    client = client or _client()
    with training_file.open("rb") as handle:
        training = client.files.create(file=handle, purpose="fine-tune")
    request = {"model": settings.fine_tuning_base_model, "training_file": training.id, "suffix": "jobel-croquis"}
    if validation_file and validation_file.stat().st_size:
        with validation_file.open("rb") as handle:
            validation = client.files.create(file=handle, purpose="fine-tune")
        request["validation_file"] = validation.id
    job = client.fine_tuning.jobs.create(**request)
    result = job.model_dump(mode="json") if hasattr(job, "model_dump") else dict(vars(job))
    (jobs_dir() / f"{result.get('id', 'unknown')}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return result


def retrieve_job(job_id: str, *, client=None) -> dict:
    client = client or _client()
    job = client.fine_tuning.jobs.retrieve(job_id)
    result = job.model_dump(mode="json") if hasattr(job, "model_dump") else dict(vars(job))
    (jobs_dir() / f"{job_id}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return result


def _record(case_id: str, label: TrainingLabel, images: list[Path]) -> dict:
    content: list[dict] = [{"type": "text", "text": f"Caso {case_id}: produza CroquiPlan."}]
    content.extend(
        {"type": "image_url", "image_url": {"url": image_data_url(image)}} for image in images
    )
    return {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": content},
            {"role": "assistant", "content": json.dumps(label.plan, ensure_ascii=False, separators=(",", ":"))},
        ]
    }


def _client() -> OpenAI:
    if not settings.openai_api_key:
        raise AIConfigurationError("OPENAI_API_KEY não configurada no backend.")
    return OpenAI(api_key=settings.openai_api_key, timeout=settings.openai_timeout_seconds)
