from __future__ import annotations

import base64
from pathlib import Path
from typing import Protocol

from openai import OpenAI

from .models import CroquiPlan, LocalExtraction

SYSTEM_PROMPT = """Você é o fallback técnico de um motor de croquis de redes elétricas RGE/CPFL.
Analise o projeto PDF e devolva somente o plano estruturado solicitado. O equipamento que
nomeia o croqui é o dispositivo real de isolamento da área de trabalho; não presuma que
ele é o primeiro item da tabela de manobras. Não invente identificadores: use somente
números visíveis no projeto ou fornecidos explicitamente pelo cadastro local. As coordenadas
são normalizadas entre 0 e 1. Represente uma topologia limpa e conectada, conservando
derivações, postes, equipamentos e limites da intervenção. A área de trabalho pode estar em
um equipamento diferente daquele que nomeia o croqui. Em Segment.style use primary para
rede primária tracejada, secondary para rede secundária contínua e projected para rede nova
marrom. Tipos permitidos: TR, FU, FC, RL, RG, OL e SC."""


class PlanFallback(Protocol):
    def propose(self, pdf_path: Path, extraction: LocalExtraction) -> CroquiPlan | None: ...


class OpenAIPlanFallback:
    def __init__(self, *, api_key: str, model: str, timeout: float) -> None:
        self.client = OpenAI(api_key=api_key, timeout=timeout, max_retries=2)
        self.model = model

    def propose(self, pdf_path: Path, extraction: LocalExtraction) -> CroquiPlan | None:
        encoded = base64.b64encode(pdf_path.read_bytes()).decode("ascii")
        candidates = [candidate.model_dump(mode="json") for candidate in extraction.candidates[:20]]
        user_context = (
            "Candidatos extraídos localmente (são evidências, não decisões):\n"
            f"{candidates}\n\n"
            f"Identificadores confirmados pelo cadastro: {extraction.registry_identifiers}\n\n"
            "Texto extraído do projeto, possivelmente incompleto:\n"
            f"{extraction.text[:24000]}"
        )
        response = self.client.responses.parse(
            model=self.model,
            store=False,
            input=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_file",
                            "filename": pdf_path.name,
                            "file_data": f"data:application/pdf;base64,{encoded}",
                        },
                        {"type": "input_text", "text": user_context},
                    ],
                },
            ],
            text_format=CroquiPlan,
        )
        plan = response.output_parsed
        if plan is not None:
            plan.source = "openai_fallback"
        return plan
