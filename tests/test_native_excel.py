from __future__ import annotations

import os
from pathlib import Path
from zipfile import ZipFile

import pytest

from backend.engine.excel_native import CANONICAL_SYMBOLS, build_native_workbook, inspect_template
from backend.engine.models import (
    CroquiPlan,
    EquipmentPlacement,
    Point,
    ProjectMetadata,
    Segment,
    WorkZone,
)


@pytest.mark.skipif(not os.getenv("JOBEL_REFERENCE_TEMPLATE"), reason="modelo oficial não configurado")
def test_native_symbols_are_cloned_from_official_sheet(tmp_path: Path):
    template = Path(os.environ["JOBEL_REFERENCE_TEMPLATE"])
    catalog = inspect_template(template)
    for key, official_name in CANONICAL_SYMBOLS.items():
        assert catalog[key] == official_name

    main = EquipmentPlacement(
        equipment_type="FU",
        number="1130054",
        position=Point(x=0.5, y=0.45),
        label="FU 1130054",
        main=True,
    )
    plan = CroquiPlan(
        main_equipment=main,
        equipment=[main],
        poles=[Point(x=0.3, y=0.45)],
        segments=[
            Segment(start=Point(x=0.1, y=0.45), end=Point(x=0.45, y=0.45)),
            Segment(start=Point(x=0.55, y=0.45), end=Point(x=0.9, y=0.45)),
        ],
        work_zones=[WorkZone(top_left=Point(x=0.4, y=0.32), bottom_right=Point(x=0.6, y=0.58))],
        confidence=1,
        source="manual",
    )
    output = build_native_workbook(template, tmp_path / "result.xlsx", plan, ProjectMetadata())
    with ZipFile(output) as archive:
        drawing = archive.read("xl/drawings/drawing1.xml").decode("utf-8")
    assert 'name="Group 729"' in drawing
    assert 'name="Rectangle 334"' in drawing
    assert drawing.count('name="Line 430"') == 2
    assert "Jobel Label" in drawing
