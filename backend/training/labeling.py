from __future__ import annotations

import base64
import json

from openai import OpenAI

from backend.config import settings
from backend.engine.ai_fallback import AIAnalysisError, AIConfigurationError
from backend.engine.models import CroquiPlan

from .dataset import load_manifest
from .models import TrainingLabel
from .storage import label_path

LABEL_PROMPT = """Converta o par homologado em ground truth estruturado.
O primeiro PDF é o projeto elétrico e o segundo é o croqui oficial aprovado.
Reconstrua o resultado como CroquiPlan, preservando equipamento principal, topologia, derivações,
postes, tipos de rede, área de trabalho e posições relativas. Coordenadas devem ficar entre 0 e 1.
Não descreva ícones: o exportador usa os objetos oficiais da aba Simbologia. Não invente códigos.
"""


def draft_label(case_id: str, *, client=None) -> TrainingLabel:
    manifest = load_manifest()
    case = manifest.case_map().get(case_id)
    if case is None:
        raise KeyError(f"Caso não encontrado: {case_id}")
    if not case.target_croqui_pdf:
        raise ValueError(f"Caso {case_id} não possui PDF oficial do croqui.")
    project = _pdf_content(case.project_pdf, "projeto-eletrico.pdf")
    target = _pdf_content(case.target_croqui_pdf, "croqui-oficial.pdf")
    client = client or _client()
    request: dict = {
        "model": settings.openai_model,
        "store": False,
        "input": [
            {"role": "system", "content": LABEL_PROMPT},
            {
                "role": "user",
                "content": [
                    project,
                    target,
                    {
                        "type": "input_text",
                        "text": json.dumps(
                            {
                                "case_id": case_id,
                                "expected_equipment_type": case.equipment_type,
                                "expected_equipment_code": case.equipment_code,
                            },
                            separators=(",", ":"),
                        ),
                    },
                ],
            },
        ],
        "text_format": CroquiPlan,
    }
    if settings.openai_reasoning_effort in {"low", "medium", "high", "xhigh"}:
        request["reasoning"] = {"effort": settings.openai_reasoning_effort}
    try:
        response = client.responses.parse(**request)
    except Exception as exc:
        raise AIAnalysisError(f"Falha ao rotular {case_id} ({type(exc).__name__}).") from exc
    plan = response.output_parsed
    if plan is None:
        raise AIAnalysisError(f"Rotulagem de {case_id} sem estrutura válida.")
    if not isinstance(plan, CroquiPlan):
        plan = CroquiPlan.model_validate(plan)
    warnings = _warnings(case.equipment_type, case.equipment_code, plan)
    label = TrainingLabel(
        case_id=case_id,
        status="draft",
        plan=plan.model_dump(mode="json"),
        source_response_id=str(getattr(response, "id", "") or ""),
        source_model=str(getattr(response, "model", "") or settings.openai_model),
        warnings=warnings,
    )
    _save(label)
    return label


def review_label(
    case_id: str,
    *,
    status: str,
    reviewer: str,
    notes: str = "",
    allow_warnings: bool = False,
) -> TrainingLabel:
    path = label_path(case_id)
    label = TrainingLabel.model_validate_json(path.read_text(encoding="utf-8"))
    if status not in {"draft", "approved", "rejected"}:
        raise ValueError("Status permitido: draft, approved ou rejected.")
    if status == "approved" and label.warnings and not allow_warnings:
        raise ValueError("O rótulo possui avisos; revise ou use --allow-warnings conscientemente.")
    label.status = status
    label.reviewer = reviewer.strip()
    label.review_notes = notes.strip()
    _save(label)
    return label


def _warnings(expected_type: str, expected_code: str, plan: CroquiPlan) -> list[str]:
    warnings: list[str] = []
    main = plan.main_equipment
    if main is None:
        return ["MAIN_EQUIPMENT_MISSING"]
    if str(main.equipment_type) != expected_type or main.number != expected_code:
        warnings.append(
            f"MAIN_EQUIPMENT_MISMATCH:expected={expected_type} {expected_code};"
            f"actual={main.equipment_type} {main.number}"
        )
    marked = [item for item in plan.equipment if item.main]
    if len(marked) != 1:
        warnings.append(f"MAIN_EQUIPMENT_COUNT:{len(marked)}")
    if not plan.segments:
        warnings.append("TOPOLOGY_MISSING")
    return warnings


def _pdf_content(path_value: str, filename: str) -> dict:
    from pathlib import Path

    path = Path(path_value)
    if path.stat().st_size > settings.openai_max_pdf_mb * 1024 * 1024:
        raise AIConfigurationError(f"{path.name} excede o limite configurado.")
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return {
        "type": "input_file",
        "filename": filename,
        "file_data": f"data:application/pdf;base64,{data}",
    }


def _save(label: TrainingLabel) -> None:
    label_path(label.case_id).write_text(
        json.dumps(label.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _client():
    if not settings.openai_api_key:
        raise AIConfigurationError("OPENAI_API_KEY não configurada no backend.")
    return OpenAI(
        api_key=settings.openai_api_key,
        timeout=settings.openai_timeout_seconds,
        max_retries=settings.openai_max_retries,
    )
