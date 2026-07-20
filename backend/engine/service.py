from __future__ import annotations

import json
import shutil
from pathlib import Path

from .ai_fallback import OpenAIPlanFallback, PlanFallback
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
    ValidationIssue,
)
from .office import render_preview, to_pdf, to_xls, to_xlsx
from .registry import enrich_from_registry, load_registry
from .validation import validate_plan


class CroquiEngine:
    def __init__(self, settings, fallback: PlanFallback | None = None) -> None:
        self.settings = settings
        if fallback is not None:
            self.fallback = fallback
        elif settings.ai_enabled and settings.openai_api_key:
            self.fallback = OpenAIPlanFallback(
                api_key=settings.openai_api_key,
                model=settings.openai_model,
                timeout=settings.openai_timeout_seconds,
            )
        else:
            self.fallback = None

    def _extract(self, project: Path):
        return extract_project(
            project,
            ocr_enabled=getattr(self.settings, "ocr_enabled", True),
            tesseract_bin=getattr(self.settings, "tesseract_bin", "tesseract"),
            ocr_language=getattr(self.settings, "ocr_language", "por+eng"),
            ocr_dpi=getattr(self.settings, "ocr_dpi", 600),
        )

    def run(
        self,
        *,
        job_id: str,
        project: Path,
        template: Path | None,
        registry: Path | None = None,
        job_dir: Path,
    ) -> EngineResult:
        extraction = self._extract(project)
        if registry is not None:
            links = load_registry(
                registry,
                work_dir=job_dir / "office" / "registry",
                libreoffice_bin=self.settings.libreoffice_bin,
            )
            enrich_from_registry(extraction, links)
        plan = decide_local(
            extraction,
            threshold=self.settings.local_auto_threshold,
            minimum_gap=self.settings.local_min_gap,
        )
        validation = validate_plan(
            plan,
            extraction,
            automatic_threshold=self.settings.local_auto_threshold,
        )
        ai_used = False
        if not validation.accepted and self.fallback is not None:
            try:
                proposal = self.fallback.propose(project, extraction)
                ai_used = True
                if proposal is not None:
                    proposal_validation = validate_plan(
                        proposal,
                        extraction,
                        automatic_threshold=self.settings.local_auto_threshold,
                    )
                    # O fallback só substitui o plano local depois de aprovado
                    # pela validação determinística.
                    if proposal_validation.accepted:
                        plan, validation = proposal, proposal_validation
                    else:
                        validation.issues.extend(
                            ValidationIssue(
                                code=f"AI_{issue.code}",
                                message=f"fallback rejeitado: {issue.message}",
                                blocking=False,
                            )
                            for issue in proposal_validation.issues
                        )
            except Exception as exc:  # resposta externa nunca derruba o motor local
                validation.issues.append(
                    ValidationIssue(
                        code="AI_FALLBACK_ERROR",
                        message=f"fallback indisponível: {type(exc).__name__}",
                        blocking=False,
                    )
                )

        result = EngineResult(
            job_id=job_id,
            status="READY_TO_GENERATE" if validation.accepted else "NEEDS_REVIEW",
            extraction=extraction,
            plan=plan,
            validation=validation,
            ai_used=ai_used,
        )
        if validation.accepted:
            if template is None:
                result.status = "TEMPLATE_REQUIRED"
            else:
                self._export(result, template, job_dir)
        self._write_report(result, job_dir)
        return result

    def override(
        self,
        *,
        job_id: str,
        project: Path,
        template: Path | None,
        registry: Path | None = None,
        job_dir: Path,
        equipment_type: str,
        number: str,
        observations: str = "",
    ) -> EngineResult:
        extraction = self._extract(project)
        if registry is not None:
            links = load_registry(
                registry,
                work_dir=job_dir / "office" / "registry",
                libreoffice_bin=self.settings.libreoffice_bin,
            )
            enrich_from_registry(extraction, links)
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
        if validation.accepted and template is not None:
            self._export(result, template, job_dir)
        elif validation.accepted:
            result.status = "TEMPLATE_REQUIRED"
        self._write_report(result, job_dir)
        return result

    def _export(self, result: EngineResult, template: Path, job_dir: Path) -> None:
        converted_dir = job_dir / "office"
        template_xlsx = to_xlsx(template, converted_dir, self.settings.libreoffice_bin)
        inspect_template(template_xlsx)
        main = result.plan.main_equipment
        basename = f"croqui {main.equipment_type} {main.number}"
        xlsx = build_native_workbook(
            template_xlsx,
            job_dir / f"{basename}.xlsx",
            result.plan,
            result.extraction.metadata,
        )
        xls_generated = to_xls(xlsx, converted_dir, self.settings.libreoffice_bin)
        xls = job_dir / f"{basename}.xls"
        shutil.copy2(xls_generated, xls)
        pdf = to_pdf(xlsx, job_dir, self.settings.libreoffice_bin)
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
