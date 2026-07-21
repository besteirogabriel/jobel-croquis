from __future__ import annotations

import base64
import hashlib
from pathlib import Path

import fitz

from backend.config import settings

from .storage import assets_dir


def render_pdf_images(case_id: str, kind: str, pdf_path: Path, *, max_pages: int = 1) -> list[Path]:
    digest = _digest(pdf_path)
    output_dir = assets_dir(case_id) / digest
    output_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(output_dir.glob(f"{kind}_p*.jpg"))
    if existing:
        return existing[:max_pages]
    results: list[Path] = []
    with fitz.open(pdf_path) as document:
        for index in range(min(len(document), max_pages)):
            page = document[index]
            long_edge = max(float(page.rect.width), float(page.rect.height), 1.0)
            zoom = max(settings.training_image_long_edge / long_edge, 0.5)
            pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            destination = output_dir / f"{kind}_p{index + 1}.jpg"
            pixmap.save(
                destination,
                output="jpg",
                jpg_quality=max(50, min(settings.training_image_quality, 95)),
            )
            results.append(destination)
    return results


def image_data_url(path: Path) -> str:
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(str(path.resolve()).encode())
    digest.update(str(path.stat().st_size).encode())
    digest.update(str(path.stat().st_mtime_ns).encode())
    digest.update(str(settings.training_image_long_edge).encode())
    digest.update(str(settings.training_image_quality).encode())
    return digest.hexdigest()[:16]
