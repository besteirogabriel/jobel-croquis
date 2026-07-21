from __future__ import annotations

import inspect
import json
import logging
import shutil
from pathlib import Path
from time import perf_counter

from .ai_fallback import (
    AIAnalysisError,
    AIConfigurationError,
    CodexPlanFallback,
    OpenAIPlanFallback,
    PlanFallback,
)
from .decision import decide_local
from .excel_native import build_native_workbook, inspect_template
from .extraction import extract_project
from .models import (
    CroquiPlan,
    EngineArtifacts,
    EngineResult,
    EquipmentPlacement,
    EquipmentType,
    Point,
)
from .office import render_preview, to_pdf, to_xls, to_xlsx
from .registry import enrich_from_registry, load_registry
from .validation import validate_plan

logger = logging.getLogger(__name__)


class JobTelemetry:
    def __init__(self, job_id: str, job_dir: Path) -> None:
        self.job_id = job_id
        self.job_dir = job_dir
        self.started = perf_counter()
        self.steps: list[dict[str, float | str]] = []

    def record(self, name: str, elapsed: float) -> None:
        self.steps.append({"etapa": name, "segundos": round(max(elapsed, 0.0), 3)})

    def measure(self, name: str):
        telemetry = self

        class _Timer:
            def __enter__(self):
                self.started = perf_counter()
                return self

            def __exit__(self, exc_type, exc, tb):
                telemetry.record(name, perf_counter() - self.started)

        return _Timer()

    def finish(self) -> None:
        self.record("tempo_total", perf_counter() - self.started)
        payload = {"job_id": self.job_id, "etapas": self.steps}
        internal_dir = self.job_dir / ".analysis"
        internal_dir.mkdir(parents=True, exist_ok=True)
        (internal_dir / "telemetria.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("telemetria do job %s: %s", self.job_id, payload["etapas"])


class CroquiEngine:
    def __init__(self, settings, fallback: PlanFallback | None = None) -> None:
        self.settings = settings
        if fallback is not None:
            self.fallback = fallback
        elif settings.ai_enabled:
            provider = getattr(settings, "ai_provider", "openai").strip().lower()
            if provider == "codex":
                self.fallback = CodexPlanFallback(
                    binary=getattr(settings, "codex_bin", "codex"),
                    model=getattr(settings, "codex_model", "gpt-5.6-sol"),
                    reasoning_effort=getattr(settings, "codex_reasoning_effort", "high"),
                    timeout=getattr(settings, "codex_timeout_seconds", 600.0),
                    max_project_pages=getattr(settings, "codex_max_project_pages", 6),
                )
            elif provider == "openai" and settings.openai_api_key:
                self.fallback = OpenAIPlanFallback(
                    api_key=settings.openai_api_key,
                    model=getattr(settings, "analysis_model", settings.openai_model),
                    timeout=settings.openai_timeout_seconds,
                )
            else:
                self.fallback = None
        else:
            self.fallback = None

    def _extract(self, project: Path, telemetry: JobTelemetry | None = None):
        return extract_project(
            project,
            ocr_enabled=getattr(self.settings, "ocr_enabled", True),
            tesseract_bin=getattr(self.settings, "tesseract_bin", "tesseract"),
            ocr_language=getattr(self.settings, "ocr_language", "por+eng"),
            ocr_dpi=getattr(self.settings, "ocr_dpi", 600),
            telemetry=telemetry.record if telemetry is not None else None,
        )

    def run(
        self,
        *,
        job_id: str,
        project: Path,
        template: Path,
        registry: Path | None = None,
        job_dir: Path,
    ) -> EngineResult:
        telemetry = JobTelemetry(job_id, job_dir)
        try:
            with telemetry.measure("extracao_total"):
                extraction = self._extract(project, telemetry)
            if registry is not None:
                with telemetry.measure("carga_cadastro_rede"):
                    links = load_registry(
                        registry,
                        work_dir=job_dir / "office" / "registry",
                        libreoffice_bin=self.settings.libreoffice_bin,
                    )
                    enrich_from_registry(extraction, links)
            with telemetry.measure("decisao_local"):
                local_plan = decide_local(
                    extraction,
                    threshold=self.settings.local_auto_threshold,
                    minimum_gap=self.settings.local_min_gap,
                )
            with telemetry.measure("validacao_local"):
                local_validation = validate_plan(
                    local_plan,
                    extraction,
                    automatic_threshold=self.settings.local_auto_threshold,
                )
            plan = local_plan
            validation = local_validation
            ai_used = False
            if self._can_use_local_fast_path(local_plan, local_validation):
                telemetry.record("analise_complementar_pulada", 0.0)
                result = EngineResult(
                    job_id=job_id,
                    status="READY_TO_GENERATE",
                    extraction=extraction,
                    plan=plan,
                    validation=validation,
                    ai_used=ai_used,
                )
                self._export(result, template, job_dir, telemetry)
                self._write_report(result, job_dir)
                return result
            if self.fallback is not None:
                try:
                    proposal = self._propose(project, extraction, telemetry)
                    ai_used = True
                    if proposal is None:
                        raise AIAnalysisError("Plano automático ausente.")
                    plan = proposal
                    with telemetry.measure("validacao"):
                        proposal_validation = validate_plan(
                            proposal,
                            extraction,
                            automatic_threshold=self.settings.local_auto_threshold,
                        )
                    validation = proposal_validation
                    if not local_validation.accepted:
                        plan.rationale.append("pré-análise local exigia revisão")
                except Exception:
                    if getattr(self.settings, "ai_required", False):
                        raise
                    plan, validation = local_plan, local_validation
            elif getattr(self.settings, "ai_required", False):
                raise AIConfigurationError("Análise técnica backend não configurada.")

            result = EngineResult(
                job_id=job_id,
                status="READY_TO_GENERATE" if validation.accepted else "NEEDS_REVIEW",
                extraction=extraction,
                plan=plan,
                validation=validation,
                ai_used=ai_used,
            )
            if validation.accepted:
                self._export(result, template, job_dir, telemetry)
            self._write_report(result, job_dir)
            return result
        finally:
            telemetry.finish()

    def _propose(self, project: Path, extraction, telemetry: JobTelemetry) -> CroquiPlan | None:
        parameters = inspect.signature(self.fallback.propose).parameters
        if "telemetry" in parameters:
            return self.fallback.propose(project, extraction, telemetry=telemetry.record)
        with telemetry.measure("subprocesso_analise_total"):
            return self.fallback.propose(project, extraction)

    def _can_use_local_fast_path(self, plan: CroquiPlan, validation) -> bool:
        if not getattr(self.settings, "local_fast_path_enabled", True):
            return False
        if not validation.accepted or plan.main_equipment is None:
            return False
        threshold = getattr(self.settings, "local_fast_path_threshold", 0.9)
        return plan.confidence >= threshold

    def override(
        self,
        *,
        job_id: str,
        project: Path,
        template: Path,
        registry: Path | None = None,
        job_dir: Path,
        equipment_type: str,
        number: str,
        observations: str = "",
    ) -> EngineResult:
        telemetry = JobTelemetry(job_id, job_dir)
        try:
            with telemetry.measure("extracao_total"):
                extraction = self._extract(project, telemetry)
            if registry is not None:
                with telemetry.measure("carga_cadastro_rede"):
                    links = load_registry(
                        registry,
                        work_dir=job_dir / "office" / "registry",
                        libreoffice_bin=self.settings.libreoffice_bin,
                    )
                    enrich_from_registry(extraction, links)
            with telemetry.measure("decisao_local"):
                base = decide_local(
                    extraction,
                    threshold=self.settings.local_auto_threshold,
                    minimum_gap=self.settings.local_min_gap,
                )
            selected_type = EquipmentType(equipment_type.upper())
            candidate = next(
                (
                    item
                    for item in extraction.candidates
                    if item.number == number
                    and item.equipment_type == selected_type
                    and item.position is not None
                ),
                None,
            )
            position = candidate.position if candidate is not None else Point(x=0.5, y=0.48)
            # As posições dos candidatos vêm do PDF; o plano local já está no canvas.
            if base.main_equipment is not None and base.main_equipment.number == number:
                position = base.main_equipment.position
            main = EquipmentPlacement(
                equipment_type=selected_type,
                number=number,
                position=position,
                label=f"{selected_type} {number}",
                main=True,
            )
            others = [item.model_copy(update={"main": False}) for item in base.equipment if item.number != number]
            plan = CroquiPlan(
                main_equipment=main,
                equipment=[main, *others],
                poles=base.poles,
                segments=base.segments,
                work_zones=base.work_zones,
                confidence=1,
                source="manual",
                rationale=["equipamento confirmado pelo engenheiro", observations]
                if observations
                else ["equipamento confirmado pelo engenheiro"],
            )
            with telemetry.measure("validacao"):
                validation = validate_plan(
                    plan,
                    extraction,
                    automatic_threshold=self.settings.local_auto_threshold,
                    allow_manual_number=True,
                )
            result = EngineResult(
                job_id=job_id,
                status="READY_TO_GENERATE" if validation.accepted else "NEEDS_REVIEW",
                extraction=extraction,
                plan=plan,
                validation=validation,
            )
            if validation.accepted:
                self._export(result, template, job_dir, telemetry)
            self._write_report(result, job_dir)
            return result
        finally:
            telemetry.finish()

    def _export(
        self,
        result: EngineResult,
        template: Path,
        job_dir: Path,
        telemetry: JobTelemetry | None = None,
    ) -> None:
        converted_dir = job_dir / "office"
        timer = telemetry.measure if telemetry is not None else _null_measure
        with timer("geracao_xlsx_template"):
            template_xlsx = to_xlsx(template, converted_dir, self.settings.libreoffice_bin)
            inspect_template(template_xlsx)
        main = result.plan.main_equipment
        basename = f"croqui {main.equipment_type} {main.number}"
        with timer("geracao_xlsx"):
            xlsx = build_native_workbook(
                template_xlsx,
                job_dir / f"{basename}.xlsx",
                result.plan,
                result.extraction.metadata,
            )
        with timer("conversao_xls"):
            xls_generated = to_xls(xlsx, converted_dir, self.settings.libreoffice_bin)
        xls = job_dir / f"{basename}.xls"
        shutil.copy2(xls_generated, xls)
        with timer("conversao_pdf"):
            pdf = to_pdf(xlsx, job_dir, self.settings.libreoffice_bin)
        with timer("preview"):
            preview = render_preview(pdf, job_dir / f"{basename}.png")
        result.status = "GENERATED"
        result.artifacts = EngineArtifacts(xlsx=xlsx, xls=xls, pdf=pdf, preview=preview)

    @staticmethod
    def _write_report(result: EngineResult, job_dir: Path) -> None:
        report = job_dir / "relatorio.json"
        result.artifacts.report = report
        report.write_text(
            json.dumps(result.public_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


class _null_measure:
    def __init__(self, name: str) -> None:
        self.name = name

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None
