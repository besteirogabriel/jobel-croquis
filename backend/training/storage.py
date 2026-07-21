from __future__ import annotations

from pathlib import Path

from backend.config import BACKEND_DIR, settings

ROOT_DIR = BACKEND_DIR.parent


def resolve_path(path: str | Path) -> Path:
    raw = Path(path)
    return raw if raw.is_absolute() else ROOT_DIR / raw


def training_dir() -> Path:
    path = resolve_path(settings.training_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def manifest_path() -> Path:
    return training_dir() / "manifest.json"


def labels_dir() -> Path:
    path = training_dir() / "labels"
    path.mkdir(parents=True, exist_ok=True)
    return path


def label_path(case_id: str) -> Path:
    return labels_dir() / f"{case_id}.json"


def assets_dir(case_id: str | None = None) -> Path:
    path = training_dir() / "assets"
    if case_id:
        path /= case_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def datasets_dir() -> Path:
    path = training_dir() / "datasets"
    path.mkdir(parents=True, exist_ok=True)
    return path


def jobs_dir() -> Path:
    path = training_dir() / "jobs"
    path.mkdir(parents=True, exist_ok=True)
    return path
