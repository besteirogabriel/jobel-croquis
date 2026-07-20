from __future__ import annotations
import json, os, re, shutil, uuid
from pathlib import Path
import fitz
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    openai_api_key: str = ""
    openai_model: str = "gpt-5.6"
    ai_enabled: bool = False
    max_upload_mb: int = 50
    data_dir: str = "/data"
    class Config: env_file = ".env"

cfg=Settings(); root=Path(cfg.data_dir); root.mkdir(parents=True,exist_ok=True)
app=FastAPI(title="Jobel Croquis",docs_url=None,redoc_url=None)

def extract(pdf:Path):
    doc=fitz.open(pdf); text="\n".join(p.get_text() for p in doc)
    def one(pattern,default=""):
        m=re.search(pattern,text,re.I|re.M); return m.group(1).strip() if m else default
    action=[]
    for m in re.finditer(r"(Abrir|Fechar)\s+(Transformador|Religador|Chave|Fus[ií]vel)\s+(\d{5,8})",text,re.I):
        action.append({"acao":m.group(1).title(),"tipo":m.group(2).title(),"numero":m.group(3)})
    ids=sorted(set(re.findall(r"\b\d{6,7}\b",text)))
    return {
      "municipio":one(r"Munic[ií]pio:\s*([^\n]+)"),
      "data_projeto":one(r"Data:\s*(\d{2}/\d{2}/\d{4})"),
      "nota":one(r"Nota:\s*(\d{9,12})"),
      "obra":one(r"Obra:\s*([^\n]+)"),
      "levantador":one(r"Levantador:\s*([^\n]+)"),
      "acoes":action,"identificadores":ids,
      "alertas":["O equipamento que nomeia o croqui deve ser o dispositivo de isolamento, não necessariamente o item da tabela de manobras."],
      "pages":len(doc)
    }

@app.get("/api/health")
def health(): return {"ok":True,"ai":"disponivel" if cfg.ai_enabled and cfg.openai_api_key else "offline"}

@app.post("/api/analisar")
async def analisar(projeto:UploadFile=File(...), modelo:UploadFile|None=File(None), cadastro:UploadFile|None=File(None)):
    if not projeto.filename.lower().endswith('.pdf'): raise HTTPException(400,"Envie um projeto PDF")
    jid=uuid.uuid4().hex[:12]; folder=root/jid; folder.mkdir()
    p=folder/"projeto.pdf"
    with p.open('wb') as f: shutil.copyfileobj(projeto.file,f)
    for up,name in ((modelo,"modelo"),(cadastro,"cadastro")):
        if up:
            dest=folder/(name+Path(up.filename).suffix.lower())
            with dest.open('wb') as f: shutil.copyfileobj(up.file,f)
    result=extract(p); result.update({"job_id":jid,"equipamento_isolamento":"","tipo_isolamento":""})
    (folder/"analise.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result

@app.post("/api/confirmar")
def confirmar(job_id:str=Form(...),tipo:str=Form(...),numero:str=Form(...),observacoes:str=Form("")):
    folder=root/job_id; path=folder/"analise.json"
    if not path.exists(): raise HTTPException(404,"Análise não encontrada")
    data=json.loads(path.read_text(encoding='utf-8'))
    data.update({"tipo_isolamento":tipo.upper(),"equipamento_isolamento":numero,"observacoes":observacoes,"status":"confirmado"})
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    return data

@app.get("/api/relatorio/{job_id}")
def relatorio(job_id:str):
    p=root/job_id/"analise.json"
    if not p.exists(): raise HTTPException(404,"Arquivo não encontrado")
    return FileResponse(p,filename=f"jobel_analise_{job_id}.json")

app.mount("/assets",StaticFiles(directory="frontend/assets"),name="assets")
@app.get("/{path:path}")
def ui(path:str=""): return FileResponse("frontend/index.html")

