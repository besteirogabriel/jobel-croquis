from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    """Configurações exclusivamente do backend.

    Credenciais e detalhes do provedor nunca são serializados pelas rotas públicas.
    A análise visual acontece exclusivamente no backend.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ai_provider: str = "codex"
    openai_api_key: str = ""
    openai_model: str = "gpt-5.6-sol"
    openai_fine_tuned_model: str = ""
    openai_reasoning_effort: str = "high"
    openai_timeout_seconds: float = 180.0
    openai_max_retries: int = 2
    openai_max_pdf_mb: int = 45
    codex_bin: str = "codex"
    codex_model: str = "gpt-5.6-sol"
    codex_reasoning_effort: str = "low"
    codex_timeout_seconds: float = 120.0
    codex_max_project_pages: int = 2
    codex_project_detail_tiles: bool = True
    codex_identifier_crop_limit: int = 4
    ai_enabled: bool = True
    ai_required: bool = True
    corpus_references_enabled: bool = True
    corpus_reference_limit: int = 1
    corpus_reference_splits: str = "train,validation"
    corpus_path: Path = Path("CROQUI IA")
    training_dir: Path = Path("/data/training")
    training_split_seed: str = "jobel-croquis-v1"
    training_image_long_edge: int = 1600
    training_image_quality: int = 84
    fine_tuning_enabled: bool = False
    fine_tuning_base_model: str = ""
    fine_tuning_max_project_pages: int = 4
    local_auto_threshold: float = 0.82
    assisted_generation_threshold: float = 0.0
    local_min_gap: float = 0.12
    local_fast_path_enabled: bool = False
    local_fast_path_threshold: float = 0.9
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

    @property
    def analysis_model(self) -> str:
        return self.openai_fine_tuned_model or self.openai_model

    @property
    def allowed_reference_splits(self) -> tuple[str, ...]:
        values = tuple(
            value.strip().lower()
            for value in self.corpus_reference_splits.split(",")
            if value.strip().lower() in {"train", "validation"}
        )
        return values or ("train",)


settings = Settings()
