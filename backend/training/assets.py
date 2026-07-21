from __future__ import annotations

import base64
import hashlib
from pathlib import Path

import fitz

from backend.config import settings

from .storage import assets_dir


def render_pdf_images(
    case_id: str,
    kind: str,
    pdf_path: Path,
    *,
    max_pages: int = 1,
    first_page_detail_tiles: bool = False,
) -> list[Path]:
    digest = _digest(pdf_path)
    output_dir = assets_dir(case_id) / digest
    output_dir.mkdir(parents=True, exist_ok=True)
    overview_paths = [output_dir / f"{kind}_p{page}.jpg" for page in range(1, max_pages + 1)]
    tile_paths = [
        output_dir / f"{kind}_p1_tile_r{row}c{column}.jpg"
        for row in (1, 2)
        for column in (1, 2)
    ]
    cached_overviews = [path for path in overview_paths if path.is_file()]
    cached_tiles = [path for path in tile_paths if path.is_file()]
    if cached_overviews and (
        not first_page_detail_tiles or len(cached_tiles) == len(tile_paths)
    ):
        return [*cached_overviews, *cached_tiles] if first_page_detail_tiles else cached_overviews
    results: list[Path] = []
    with fitz.open(pdf_path) as document:
        for index in range(min(len(document), max_pages)):
            page = document[index]
            destination = output_dir / f"{kind}_p{index + 1}.jpg"
            _render_page_region(page, page.rect, destination)
            results.append(destination)
        if first_page_detail_tiles and document:
            page = document[0]
            for row, column, clip in _detail_clips(page.rect):
                destination = output_dir / f"{kind}_p1_tile_r{row}c{column}.jpg"
                _render_page_region(page, clip, destination)
                results.append(destination)
    return results


def render_identifier_crops(
    case_id: str,
    kind: str,
    pdf_path: Path,
    identifiers: list[str],
    *,
    limit: int = 8,
) -> list[Path]:
    output_dir = assets_dir(case_id) / _digest(pdf_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    results: list[Path] = []
    with fitz.open(pdf_path) as document:
        for identifier in dict.fromkeys(identifiers):
            matches: list[tuple[float, int, fitz.Rect]] = []
            for page_index, page in enumerate(document):
                for word in page.get_text("words", sort=False):
                    if str(word[4]).strip(".,;:()[]") != identifier:
                        continue
                    center_y = ((word[1] + word[3]) / 2) / max(page.rect.height, 1)
                    score = (1 if center_y > 0.78 else 0) + abs(center_y - 0.5)
                    matches.append((score, page_index, fitz.Rect(word[:4])))
            if not matches:
                continue
            _, page_index, word_rect = min(matches, key=lambda item: item[0])
            page = document[page_index]
            center = (word_rect.tl + word_rect.br) / 2
            half_width = page.rect.width * 0.12
            half_height = page.rect.height * 0.14
            clip = fitz.Rect(
                max(page.rect.x0, center.x - half_width),
                max(page.rect.y0, center.y - half_height),
                min(page.rect.x1, center.x + half_width),
                min(page.rect.y1, center.y + half_height),
            )
            destination = output_dir / f"{kind}_focus_{identifier}_p{page_index + 1}.jpg"
            if not destination.is_file():
                _render_page_region(page, clip, destination)
            results.append(destination)
            if len(results) >= max(0, limit):
                break
    return results


def _render_page_region(page: fitz.Page, clip: fitz.Rect, destination: Path) -> None:
    long_edge = max(float(clip.width), float(clip.height), 1.0)
    zoom = max(settings.training_image_long_edge / long_edge, 0.5)
    pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, alpha=False)
    pixmap.save(
        destination,
        output="jpg",
        jpg_quality=max(50, min(settings.training_image_quality, 95)),
    )


def _detail_clips(rect: fitz.Rect) -> list[tuple[int, int, fitz.Rect]]:
    overlap = 0.08
    tile_width = rect.width * (0.5 + overlap / 2)
    tile_height = rect.height * (0.5 + overlap / 2)
    x_positions = (rect.x0, rect.x1 - tile_width)
    y_positions = (rect.y0, rect.y1 - tile_height)
    return [
        (
            row + 1,
            column + 1,
            fitz.Rect(x, y, x + tile_width, y + tile_height),
        )
        for row, y in enumerate(y_positions)
        for column, x in enumerate(x_positions)
    ]


def image_data_url(path: Path) -> str:
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    media_type = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return f"data:{media_type};base64,{encoded}"


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(str(path.resolve()).encode())
    digest.update(str(path.stat().st_size).encode())
    digest.update(str(path.stat().st_mtime_ns).encode())
    digest.update(str(settings.training_image_long_edge).encode())
    digest.update(str(settings.training_image_quality).encode())
    return digest.hexdigest()[:16]
