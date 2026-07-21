from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Protocol

from openai import OpenAI

from backend.config import settings
from backend.training.assets import image_data_url
from backend.training.retrieval import RetrievedReference, retrieve_reference_cases

from .models import CroquiPlan, LocalExtraction

SYSTEM_PROMPT = """Você é o analisador técnico principal de croquis de isolamento RGE/CPFL.
Analise integralmente o projeto elétrico e devolva somente o CroquiPlan estruturado solicitado.

Regras obrigatórias:
1. Identifique o dispositivo real que define e nomeia o isolamento. Não presuma que seja o primeiro
   equipamento da tabela de manobras.
2. Nunca invente identificadores; use apenas números visíveis no projeto ou confirmados no cadastro.
3. Preserve a topologia elétrica útil: montante, jusante, derivações, postes, equipamentos e redes.
4. Tipos permitidos: TR, FU, FC, RL, RG, OL e SC.
5. Coordenadas são normalizadas entre 0 e 1 e devem compor um croqui legível, não reproduzir o carimbo.
6. Segment.style: primary para rede primária tracejada, secondary para secundária contínua e projected
   para rede nova marrom.
7. O equipamento principal deve aparecer exatamente uma vez com main=true e ligado à rede.
8. Casos oficiais semelhantes demonstram convenções, mas não autorizam copiar códigos ou uma topologia
   que não exista no projeto atual.
9. Não desenhe ou descreva ícones. O backend clona os objetos oficiais da aba Simbologia do Excel.
10. Em incerteza, reduza confidence e registre a justificativa em rationale.
"""


class AIConfigurationError(RuntimeError):
    """A análise backend obrigatória não está configurada."""


class AIAnalysisError(RuntimeError):
    """A análise backend não devolveu um plano técnico utilizável."""


class PlanFallback(Protocol):
    def propose(self, pdf_path: Path, extraction: LocalExtraction) -> CroquiPlan | None: ...


class OpenAIPlanFallback:
    """Analisador visual backend. O nome legado preserva compatibilidade interna."""

    def __init__(self, *, api_key: str, model: str, timeout: float) -> None:
        self.client = OpenAI(
            api_key=api_key,
            timeout=timeout,
            max_retries=settings.openai_max_retries,
        )
        self.model = model

    def propose(self, pdf_path: Path, extraction: LocalExtraction) -> CroquiPlan | None:
        if pdf_path.stat().st_size > settings.openai_max_pdf_mb * 1024 * 1024:
            raise AIConfigurationError("Projeto excede o limite técnico configurado no backend.")
        encoded = base64.b64encode(pdf_path.read_bytes()).decode("ascii")
        references = retrieve_reference_cases(pdf_path, extraction)
        content: list[dict] = [
            {
                "type": "input_file",
                "filename": pdf_path.name,
                "file_data": f"data:application/pdf;base64,{encoded}",
            },
            {"type": "input_text", "text": _technical_context(extraction, references)},
        ]
        content.extend(_reference_content(references))
        request: dict = {
            "model": self.model,
            "store": False,
            "input": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
            "text_format": CroquiPlan,
        }
        if settings.openai_reasoning_effort in {"low", "medium", "high", "xhigh"}:
            request["reasoning"] = {"effort": settings.openai_reasoning_effort}
        try:
            response = self.client.responses.parse(**request)
        except Exception as exc:
            raise AIAnalysisError(f"Serviço de análise indisponível ({type(exc).__name__}).") from exc
        plan = response.output_parsed
        if plan is None:
            raise AIAnalysisError("O analisador não devolveu um plano estruturado.")
        if not isinstance(plan, CroquiPlan):
            plan = CroquiPlan.model_validate(plan)
        plan.source = "automatic"
        return plan


def _technical_context(
    extraction: LocalExtraction,
    references: list[RetrievedReference],
) -> str:
    data = {
        "metadados": extraction.metadata.model_dump(mode="json"),
        "identificadores_no_projeto": extraction.identifiers,
        "identificadores_confirmados_no_cadastro": extraction.registry_identifiers,
        "acoes_de_manobra": [item.model_dump(mode="json") for item in extraction.actions],
        "candidatos_locais_nao_decisivos": [
            item.model_dump(mode="json") for item in extraction.candidates[:30]
        ],
        "referencias_oficiais": [
            {
                "ordem": index,
                "tipo": item.equipment_type,
                "formato": "par projeto/croqui",
            }
            for index, item in enumerate(references, start=1)
        ],
        "texto_extraido_possivelmente_incompleto": extraction.text[:30000],
    }
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _reference_content(references: list[RetrievedReference]) -> list[dict]:
    content: list[dict] = []
    for index, reference in enumerate(references, start=1):
        content.append(
            {
                "type": "input_text",
                "text": f"Referência oficial {index}: primeiro o projeto e depois o croqui aprovado.",
            }
        )
        for image in reference.project_images:
            content.append(
                {"type": "input_image", "image_url": image_data_url(image), "detail": "high"}
            )
        for image in reference.target_images:
            content.append(
                {"type": "input_image", "image_url": image_data_url(image), "detail": "high"}
            )
    return content
