from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    """Configurações exclusivamente do backend.

    A chave da OpenAI nunca é serializada pelas rotas públicas. A IA é um
    fallback: o motor local sempre roda e é validado antes de qualquer chamada.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openai_api_key: str = ""
    openai_model: str = "gpt-5.6-sol"
    openai_timeout_seconds: float = 90.0
    ai_enabled: bool = False
    local_auto_threshold: float = 0.82
    local_min_gap: float = 0.12
    max_upload_mb: int = 50
    expose_api_docs: bool = False
    data_dir: Path = Path("/data")
    official_template_path: Path = BACKEND_DIR / "assets" / "modelo_croqui_oficial.xlsx"
    network_registry_path: Path | None = None
    libreoffice_bin: str = "soffice"
    pdftoppm_bin: str = "pdftoppm"
    ocr_enabled: bool = True
    tesseract_bin: str = "tesseract"
    ocr_language: str = "por+eng"
    ocr_dpi: int = 600


settings = Settings()
