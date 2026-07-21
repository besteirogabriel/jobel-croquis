from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
from pathlib import Path
from time import perf_counter
from typing import Protocol

from openai import OpenAI

from backend.config import BACKEND_DIR, settings
from backend.training.assets import image_data_url, render_identifier_crops, render_pdf_images
from backend.training.retrieval import RetrievedReference, retrieve_reference_cases

from .models import CroquiPlan, LocalExtraction

SYSTEM_PROMPT = """Você é o analisador técnico principal de croquis de isolamento RGE/CPFL.
Analise integralmente o projeto elétrico e devolva somente o CroquiPlan estruturado solicitado.

Regras obrigatórias:
1. Identifique o dispositivo real que define e nomeia o isolamento. Não presuma que seja o primeiro
   equipamento da tabela de manobras.
2. Nunca invente identificadores; use apenas números visíveis no projeto ou confirmados no cadastro.
3. Preserve a topologia elétrica útil: montante, jusante, derivações, postes, equipamentos e redes.
4. Equipamentos numerados permitidos: TR, FU, FC, RL, RG, OL e SC.
5. Coordenadas são normalizadas entre 0 e 1 e devem compor um croqui legível, não reproduzir o carimbo.
6. Segment.style: primary para rede primária tracejada, secondary para secundária contínua e projected
   para rede nova marrom.
7. O equipamento principal deve aparecer exatamente uma vez com main=true e ligado à rede.
8. Casos oficiais semelhantes demonstram convenções, mas não autorizam copiar códigos ou uma topologia
   que não exista no projeto atual.
9. Não desenhe ou descreva ícones. O backend clona os objetos oficiais da aba Simbologia do Excel.
10. Em incerteza, reduza confidence e registre a justificativa em rationale.
11. Inspecione e inclua todos os símbolos técnicos visíveis. Use symbols para poste novo,
    cruzamentos, passagens, mudanças de bitola, seccionamentos, transformador particular,
    capacitor, fusíveis especiais, chaves faca, omni-rupter, aterramentos BT/AT e área oval.
12. Não substitua símbolos por postes ou linhas. Cada posição em poles representa somente um
    poste existente; cada equipamento e cada item de symbols deve corresponder a um objeto visível.
13. Preserve todos os postes intermediários dos trechos representados; não reduza uma sequência de
    postes a apenas suas extremidades. Conte visualmente círculos, círculos concêntricos e quadrados.
14. Compare chaves com a legenda: fusível, faca com abertura em carga, faca sem abertura e versões
    tripolares são classes distintas. FC usa por padrão a faca com abertura em carga; use
    KNIFE_NO_LOAD_BREAK quando a planta mostrar explicitamente a variante sem abertura. Registre o
    número legível em label e nunca troque chave por TR.
15. Inclua cada aterramento AT ou BT do croqui operacional e cada área de trabalho tracejada. Não
    use FUSE_NO_LOAD_BREAK para representar aterramento ou proteção genérica de transformador.
16. O retângulo/oval de trabalho delimita a intervenção, não toda a rede. Em projetos com uma região
    de intervenção inequivocamente indicada, work_zones não pode ficar vazio.
17. Determine primeiro a região e o nível de tensão da intervenção e siga a rede em direção à fonte.
    Trabalho restrito à rede secundária é normalmente nomeado pelo TR que a alimenta; trabalho no TR
    ou na rede primária exige o dispositivo de isolamento imediatamente a montante. Uma manobra remota
    ou auxiliar pode constar da tabela sem nomear o croqui.
18. Use as referências para aprender essa convenção operacional, nunca para transferir identificadores.
    Se o dispositivo a montante estiver fora da folha e não houver cadastro confirmado, não deduza seu
    número: escolha baixa confidence e explique a ausência. Mantenha label curto e técnico.
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
            {
                "type": "input_text",
                "text": "Legenda oficial de simbologia; use somente para reconhecer classes:",
            },
            {
                "type": "input_image",
                "image_url": image_data_url(_symbol_legend_path()),
                "detail": "high",
            },
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


class CodexPlanFallback:
    """Analisador local que usa a assinatura do ChatGPT autenticada no Codex CLI."""

    def __init__(
        self,
        *,
        binary: str,
        model: str,
        reasoning_effort: str,
        timeout: float,
        max_project_pages: int,
    ) -> None:
        self.binary = binary
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.timeout = timeout
        self.max_project_pages = max(1, max_project_pages)

    def propose(self, pdf_path: Path, extraction: LocalExtraction, telemetry=None) -> CroquiPlan | None:
        executable = shutil.which(self.binary)
        if executable is None:
            raise AIConfigurationError("Executável do analisador local não encontrado.")

        started = perf_counter()
        references = retrieve_reference_cases(pdf_path, extraction, telemetry=telemetry)
        if telemetry is not None:
            telemetry("carga_selecao_corpus", perf_counter() - started)
        started = perf_counter()
        project_images = render_pdf_images(
            f"runtime-{pdf_path.parent.name}",
            f"current-project-p{self.max_project_pages}",
            pdf_path,
            max_pages=self.max_project_pages,
            first_page_detail_tiles=settings.codex_project_detail_tiles,
        )
        project_images.extend(
            render_identifier_crops(
                f"runtime-{pdf_path.parent.name}",
                f"current-project-p{self.max_project_pages}",
                pdf_path,
                [item.number for item in extraction.candidates],
                limit=settings.codex_identifier_crop_limit,
            )
        )
        if telemetry is not None:
            telemetry("renderizacao_projeto", perf_counter() - started)
        if not project_images:
            raise AIAnalysisError("O projeto não pôde ser renderizado para análise visual.")

        started = perf_counter()
        attachments, attachment_manifest = _codex_attachments(project_images, references)
        if telemetry is not None:
            telemetry("preparacao_anexos", perf_counter() - started)
        internal_dir = pdf_path.parent / ".analysis"
        internal_dir.mkdir(parents=True, exist_ok=True)
        schema_path = internal_dir / "croqui-plan.schema.json"
        output_path = internal_dir / "croqui-plan.json"
        schema_path.write_text(
            json.dumps(_strict_schema(CroquiPlan.model_json_schema()), ensure_ascii=False),
            encoding="utf-8",
        )
        output_path.unlink(missing_ok=True)

        command = [
            executable,
            "--ask-for-approval",
            "never",
            "exec",
            "--skip-git-repo-check",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--disable",
            "plugins",
            "--disable",
            "remote_plugin",
            "--disable",
            "apps",
            "--disable",
            "hooks",
            "--disable",
            "multi_agent",
            "--sandbox",
            "read-only",
            "--cd",
            str(internal_dir),
            "--model",
            self.model,
            "--config",
            f'model_reasoning_effort="{self.reasoning_effort}"',
            "--config",
            'web_search="disabled"',
            "--config",
            'cli_auth_credentials_store="file"',
            "--config",
            'forced_login_method="chatgpt"',
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(output_path),
        ]
        for attachment in attachments:
            command.extend(["--image", str(attachment.resolve())])
        command.append("-")

        environment = os.environ.copy()
        # O modo Codex deve consumir a assinatura ChatGPT autenticada, mesmo que
        # uma chave de API tenha permanecido no .env de uma instalação anterior.
        for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN"):
            environment.pop(key, None)
        try:
            started = perf_counter()
            completed = subprocess.run(
                command,
                input=_codex_prompt(extraction, references, attachment_manifest),
                text=True,
                capture_output=True,
                timeout=self.timeout,
                env=environment,
                check=False,
            )
            if telemetry is not None:
                telemetry("subprocesso_analise_total", perf_counter() - started)
        except subprocess.TimeoutExpired as exc:
            if telemetry is not None:
                telemetry("subprocesso_analise_total", self.timeout)
            raise AIAnalysisError("A análise local excedeu o tempo limite configurado.") from exc
        except OSError as exc:
            raise AIConfigurationError("Não foi possível iniciar o analisador local.") from exc

        if completed.returncode != 0:
            _raise_codex_failure(completed)
        if not output_path.is_file():
            raise AIAnalysisError("O analisador local terminou sem produzir o plano estruturado.")
        try:
            plan = CroquiPlan.model_validate_json(output_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise AIAnalysisError("O analisador local devolveu um plano estruturado inválido.") from exc
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


def _codex_attachments(
    project_images: list[Path],
    references: list[RetrievedReference],
) -> tuple[list[Path], list[dict]]:
    attachments: list[Path] = []
    manifest: list[dict] = []
    for image in project_images:
        attachments.append(image)
        manifest.append(
            {
                "anexo": len(attachments),
                "conteudo": "projeto_atual",
                "vista": image.stem,
            }
        )
    attachments.append(_symbol_legend_path())
    manifest.append({"anexo": len(attachments), "conteudo": "legenda_simbologia_oficial"})
    for reference_index, reference in enumerate(references, start=1):
        for page, image in enumerate(reference.project_images, start=1):
            attachments.append(image)
            manifest.append(
                {
                    "anexo": len(attachments),
                    "conteudo": "projeto_referencia",
                    "referencia": reference_index,
                    "pagina": page,
                }
            )
        for page, image in enumerate(reference.target_images, start=1):
            attachments.append(image)
            manifest.append(
                {
                    "anexo": len(attachments),
                    "conteudo": "croqui_oficial_referencia",
                    "referencia": reference_index,
                    "pagina": page,
                }
            )
    return attachments, manifest


def _symbol_legend_path() -> Path:
    path = BACKEND_DIR / "assets" / "simbologia_oficial.png"
    if not path.is_file():
        raise AIConfigurationError("Legenda de simbologia oficial não encontrada.")
    return path


def _codex_prompt(
    extraction: LocalExtraction,
    references: list[RetrievedReference],
    attachment_manifest: list[dict],
) -> str:
    return "\n\n".join(
        [
            SYSTEM_PROMPT,
            "Esta é uma execução privada e não interativa. Analise apenas os anexos e o contexto "
            "fornecidos. Não pesquise na web, não altere arquivos e não tente obter dados externos.",
            "As vistas com 'tile' são ampliações sobrepostas da primeira página do projeto atual. "
            "Use-as para ler números e distinguir símbolos; use a vista geral para coordenadas e "
            "topologia. Vistas com 'focus_NUMERO' mostram o entorno ampliado daquele identificador; "
            "use o símbolo visível nelas para corrigir tipos locais inferidos apenas por proximidade "
            "de texto ou kVA. Não conte o mesmo objeto novamente quando aparecer em dois recortes.",
            "Ordem e significado dos anexos:\n"
            + json.dumps(attachment_manifest, ensure_ascii=False, separators=(",", ":")),
            "Contexto técnico extraído do projeto:\n" + _technical_context(extraction, references),
            "Devolva exclusivamente o objeto JSON que satisfaz o schema solicitado.",
        ]
    )


def _strict_schema(value):
    if isinstance(value, dict):
        result = {key: _strict_schema(item) for key, item in value.items() if key != "default"}
        if result.get("type") == "object" or "properties" in result:
            properties = result.get("properties", {})
            result["additionalProperties"] = False
            result["required"] = list(properties)
        return result
    if isinstance(value, list):
        return [_strict_schema(item) for item in value]
    return value


def _raise_codex_failure(completed: subprocess.CompletedProcess) -> None:
    diagnostic = f"{completed.stderr}\n{completed.stdout}".lower()
    if any(
        marker in diagnostic
        for marker in (
            "not logged in",
            "login required",
            "please run codex login",
            "authentication required",
            "unauthorized",
        )
    ):
        raise AIConfigurationError(
            "Analisador local ainda não autenticado com a conta ChatGPT."
        )
    if any(
        marker in diagnostic
        for marker in ("usage limit", "rate limit", "too many requests", "quota")
    ):
        raise AIAnalysisError("Limite temporário de uso do plano ChatGPT atingido.")
    raise AIAnalysisError(f"Analisador local encerrou com código {completed.returncode}.")
