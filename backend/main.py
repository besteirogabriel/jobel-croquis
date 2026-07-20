from __future__ import annotations

import json
import re
import shutil
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .engine import CroquiEngine
from .engine.excel_native import inspect_template
from .engine.models import EquipmentType
from .engine.office import OfficeError

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_ROOT = settings.data_dir
DATA_ROOT.mkdir(parents=True, exist_ok=True)
JOB_ID_RE = re.compile(r"^[a-f0-9]{12}$")

app = FastAPI(
    title="Jobel Croquis Engine",
    docs_url="/api/docs" if settings.expose_api_docs else None,
    redoc_url=None,
)
engine = CroquiEngine(settings)


def _job_dir(job_id: str) -> Path:
    if not JOB_ID_RE.fullmatch(job_id):
        raise HTTPException(400, "Identificador de processamento inválido")
    return DATA_ROOT / job_id


async def _save_upload(upload: UploadFile, destination: Path, allowed: set[str]) -> Path:
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in allowed:
        expected = ", ".join(sorted(allowed))
        raise HTTPException(400, f"Formato inválido. Envie: {expected}")
    maximum = settings.max_upload_mb * 1024 * 1024
    total = 0
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with destination.open("wb") as output:
            while chunk := await upload.read(1024 * 1024):
                total += len(chunk)
                if total > maximum:
                    raise HTTPException(413, f"Arquivo excede {settings.max_upload_mb} MB")
                output.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    if total == 0:
        destination.unlink(missing_ok=True)
        raise HTTPException(400, "Arquivo vazio")
    return destination


def _official_template() -> Path:
    template = settings.official_template_path
    if not template.is_absolute():
        template = BASE_DIR / template
    if not template.is_file():
        raise HTTPException(500, "Modelo oficial interno não encontrado")
    try:
        inspect_template(template)
    except Exception as exc:
        raise HTTPException(500, f"Modelo oficial interno inválido: {exc}") from exc
    return template


def _network_registry() -> Path | None:
    registry = settings.network_registry_path
    if registry is None:
        return None
    if not registry.is_absolute():
        registry = BASE_DIR / registry
    if not registry.is_file():
        raise HTTPException(500, "Cadastro de rede configurado no servidor não foi encontrado")
    if registry.suffix.lower() not in {".csv", ".xls", ".xlsx"}:
        raise HTTPException(500, "Cadastro de rede interno deve ser .csv, .xls ou .xlsx")
    return registry


def _run_engine(job_id: str, folder: Path):
    try:
        return engine.run(
            job_id=job_id,
            project=folder / "projeto.pdf",
            template=_official_template(),
            registry=_network_registry(),
            job_dir=folder,
        )
    except OfficeError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/health")
def health() -> JSONResponse:
    template_ready = False
    try:
        _official_template()
        template_ready = True
    except HTTPException:
        pass
    return JSONResponse(
        status_code=200 if template_ready else 503,
        content={
            "ok": template_ready,
            "engine": "local-first",
            "modelo_oficial": "pronto" if template_ready else "indisponivel",
            "ai": (
                "fallback_disponivel" if settings.ai_enabled and settings.openai_api_key else "offline"
            ),
        },
    )


@app.post("/api/analisar")
async def analisar(projeto: UploadFile = File(...)):
    job_id = uuid.uuid4().hex[:12]
    folder = _job_dir(job_id)
    folder.mkdir(parents=True, exist_ok=False)
    try:
        await _save_upload(projeto, folder / "projeto.pdf", {".pdf"})
        return _run_engine(job_id, folder).public_dict()
    except Exception:
        # Uploads inválidos não deixam jobs órfãos; resultados de motor são
        # preservados pelo relatório quando a execução chegou a esse ponto.
        if not (folder / "relatorio.json").exists():
            shutil.rmtree(folder, ignore_errors=True)
        raise


@app.post("/api/confirmar")
def confirmar(
    job_id: str = Form(...),
    tipo: str = Form(...),
    numero: str = Form(...),
    observacoes: str = Form(""),
):
    folder = _job_dir(job_id)
    project = folder / "projeto.pdf"
    if not project.exists():
        raise HTTPException(404, "Processamento não encontrado")
    try:
        equipment_type = EquipmentType(tipo.upper())
    except ValueError as exc:
        raise HTTPException(400, "Tipo inválido. Use TR, FU, FC, RL, RG, OL ou SC") from exc
    if not re.fullmatch(r"\d{5,8}", numero):
        raise HTTPException(400, "Número de equipamento inválido")
    try:
        result = engine.override(
            job_id=job_id,
            project=project,
            template=_official_template(),
            registry=_network_registry(),
            job_dir=folder,
            equipment_type=equipment_type,
            number=numero,
            observations=observacoes,
        )
    except OfficeError as exc:
        raise HTTPException(422, str(exc)) from exc
    return result.public_dict()


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    report = _job_dir(job_id) / "relatorio.json"
    if not report.exists():
        raise HTTPException(404, "Processamento não encontrado")
    return json.loads(report.read_text(encoding="utf-8"))


@app.get("/api/jobs/{job_id}/download/{kind}")
def download(job_id: str, kind: str):
    folder = _job_dir(job_id)
    report = folder / "relatorio.json"
    if not report.exists():
        raise HTTPException(404, "Processamento não encontrado")
    data = json.loads(report.read_text(encoding="utf-8"))
    artifact_key = "report" if kind == "report" else kind
    if artifact_key not in {"report", "xlsx", "xls", "pdf", "preview"}:
        raise HTTPException(404, "Artefato inválido")
    filename = data.get("artifacts", {}).get(artifact_key)
    path = folder / filename if filename else None
    if path is None or not path.exists() or path.parent != folder:
        raise HTTPException(404, "Artefato ainda não foi gerado")
    return FileResponse(path, filename=path.name)


@app.get("/api/relatorio/{job_id}")
def legacy_report(job_id: str):
    return download(job_id, "report")


app.mount("/assets", StaticFiles(directory=BASE_DIR / "frontend" / "assets"), name="assets")


@app.get("/{path:path}")
def ui(path: str = ""):
    return FileResponse(BASE_DIR / "frontend" / "index.html")
