from pathlib import Path

import fitz
import pytest

from backend.config import settings
from backend.training.assets import render_identifier_crops, render_pdf_images


def test_project_render_includes_overview_tiles_and_identifier_focus(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setattr(settings, "training_dir", tmp_path / "training")
    pdf = tmp_path / "project.pdf"
    document = fitz.open()
    page = document.new_page(width=1200, height=800)
    page.insert_text((620, 380), "FU 900001")
    document.save(pdf)
    document.close()

    views = render_pdf_images(
        "case",
        "project",
        pdf,
        max_pages=1,
        first_page_detail_tiles=True,
    )
    focus = render_identifier_crops("case", "project", pdf, ["900001"])

    assert len(views) == 5
    assert len(focus) == 1
    assert "focus_900001" in focus[0].stem
    assert all(path.is_file() for path in [*views, *focus])
