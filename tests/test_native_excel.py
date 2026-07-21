from __future__ import annotations

import copy
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from backend.engine.excel_native import (
    CANONICAL_SYMBOLS,
    NS_A,
    NS_XDR,
    _clone_symbol,
    _compact_equipment_label,
    _compact_symbol_label,
    _offset_and_extent,
    _outer_xfrm,
    _q,
    _symbol_anchors,
    _top_name,
    _workbook_parts,
    build_native_workbook,
    inspect_template,
)
from backend.engine.models import (
    CroquiPlan,
    EquipmentPlacement,
    Point,
    ProjectMetadata,
    Segment,
    SymbolPlacement,
    WorkZone,
)


def test_native_symbols_are_cloned_from_official_sheet(tmp_path: Path):
    template = Path("backend/assets/modelo_croqui_oficial.xlsx")
    catalog = inspect_template(template)
    for key, official_name in CANONICAL_SYMBOLS.items():
        assert catalog[key] == official_name

    main = EquipmentPlacement(
        equipment_type="FU",
        number="900001",
        position=Point(x=0.5, y=0.45),
        label="FU 900001",
        main=True,
    )
    plan = CroquiPlan(
        main_equipment=main,
        equipment=[main],
        symbols=[SymbolPlacement(symbol_type="GROUND_BT", position=Point(x=0.7, y=0.45))],
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
    assert 'name="Group 184"' in drawing
    assert 'name="Oval 148"' not in drawing
    assert 'name="Rectangle 334"' in drawing
    assert 'name="Group 72"' in drawing
    assert ">BT<" in drawing
    assert drawing.count('name="Line 430"') == 2
    assert "Jobel Label" in drawing


def test_bundled_template_is_sanitized_and_keeps_rge_logo():
    template = Path("backend/assets/modelo_croqui_oficial.xlsx")
    with ZipFile(template) as archive:
        parts = _workbook_parts(archive)
        drawing = ET.fromstring(archive.read(parts.croqui_drawing))
        assert len(list(drawing)) == 1
        assert drawing.find(f".//{_q(NS_XDR, 'pic')}") is not None
        sheet = ET.fromstring(archive.read(parts.croqui_sheet))
        viability = next(
            cell for cell in sheet.findall(".//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}c")
            if cell.attrib.get("r") == "AQ33"
        )
        assert "".join(viability.itertext()) == ""
        contents = b"\n".join(archive.read(name) for name in archive.namelist())
    assert b"CLIENTE_ORIGEM" not in contents


def _cell_anchor_signature(anchor: ET.Element) -> dict[str, tuple[int, int, int, int]]:
    signature: dict[str, tuple[int, int, int, int]] = {}
    for marker in ("from", "to"):
        node = anchor.find(_q(NS_XDR, marker))
        assert node is not None
        signature[marker] = tuple(
            int(node.find(_q(NS_XDR, field)).text or "0")
            for field in ("col", "colOff", "row", "rowOff")
        )
    return signature


def _internal_transform_signature(anchor: ET.Element) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    outer_offset = _outer_xfrm(anchor).find(_q(NS_A, "off"))
    offsets = [
        (node.attrib.get("x", "0"), node.attrib.get("y", "0"))
        for node in anchor.findall(f".//{_q(NS_A, 'off')}")
        if node is not outer_offset
    ]
    child_offsets = [
        (node.attrib.get("x", "0"), node.attrib.get("y", "0"))
        for node in anchor.findall(f".//{_q(NS_A, 'chOff')}")
    ]
    return offsets, child_offsets


def _normalized_object_xml(anchor: ET.Element) -> bytes:
    normalized = copy.deepcopy(anchor)
    for marker in ("from", "to"):
        node = normalized.find(_q(NS_XDR, marker))
        assert node is not None
        for field in ("col", "colOff", "row", "rowOff"):
            child = node.find(_q(NS_XDR, field))
            assert child is not None
            child.text = "0"
    outer_offset = _outer_xfrm(normalized).find(_q(NS_A, "off"))
    assert outer_offset is not None
    outer_offset.set("x", "0")
    outer_offset.set("y", "0")
    for index, prop in enumerate(normalized.findall(f".//{_q(NS_XDR, 'cNvPr')}")):
        prop.set("id", str(index))
    return ET.tostring(normalized)


def test_cloned_symbols_keep_official_size_offsets_and_group_geometry():
    template = Path("backend/assets/modelo_croqui_oficial.xlsx")
    with ZipFile(template) as archive:
        parts = _workbook_parts(archive)
        catalog = _symbol_anchors(archive.read(parts.symbol_drawing))

    for key in ("POLE", "TR", "FU", "FC", "RL", "RG", "OL", "SC"):
        source = catalog[key]
        clone, _ = _clone_symbol(source, Point(x=0.53, y=0.47), 900)
        clone_cells = _cell_anchor_signature(clone)

        # A caixa da âncora usa tamanho físico exato e não atravessa células;
        # assim a grade diferente da aba Croqui não deforma o símbolo.
        assert clone_cells["to"][0] == clone_cells["from"][0]
        assert clone_cells["to"][2] == clone_cells["from"][2]
        assert clone_cells["from"][1::2] == (0, 0)
        assert clone_cells["to"][1] == _offset_and_extent(source)[2]
        assert clone_cells["to"][3] == _offset_and_extent(source)[3]

        # Clonar muda posição, nunca extensão ou coordenadas internas do grupo.
        assert _offset_and_extent(clone)[2:] == _offset_and_extent(source)[2:]
        assert _internal_transform_signature(clone) == _internal_transform_signature(source)
        assert _normalized_object_xml(clone) == _normalized_object_xml(source)


def test_pole_catalog_uses_concentric_official_symbol_instead_of_plain_oval():
    template = Path("backend/assets/modelo_croqui_oficial.xlsx")
    with ZipFile(template) as archive:
        parts = _workbook_parts(archive)
        pole = _symbol_anchors(archive.read(parts.symbol_drawing))["POLE"]

    assert _top_name(pole) == "Group 184"
    assert pole.find(_q(NS_XDR, "grpSp")) is not None
    assert [
        geometry.attrib.get("prst")
        for geometry in pole.findall(f".//{_q(NS_A, 'prstGeom')}")
    ] == ["ellipse", "ellipse"]


def test_fc_uses_load_break_knife_and_keeps_no_load_variant_available(tmp_path: Path):
    template = Path("backend/assets/modelo_croqui_oficial.xlsx")
    main = EquipmentPlacement(
        equipment_type="FC",
        number="900001",
        position=Point(x=0.45, y=0.45),
        main=True,
    )
    plan = CroquiPlan(
        main_equipment=main,
        equipment=[main],
        symbols=[
            SymbolPlacement(
                symbol_type="KNIFE_NO_LOAD_BREAK",
                position=Point(x=0.65, y=0.45),
            )
        ],
        segments=[
            Segment(start=Point(x=0.2, y=0.45), end=Point(x=0.8, y=0.45))
        ],
        confidence=1,
        source="manual",
    )

    output = build_native_workbook(template, tmp_path / "fc.xlsx", plan, ProjectMetadata())

    with ZipFile(output) as archive:
        drawing = archive.read("xl/drawings/drawing1.xml").decode("utf-8")
    assert drawing.count('name="Group 319"') == 1
    assert drawing.count('name="Group 302"') == 1


def test_generated_labels_are_short_and_operational():
    transformer = EquipmentPlacement(
        equipment_type="TR",
        number="900001",
        position=Point(x=0.5, y=0.5),
        label="Transformador 900001 - 112,5 kVA; dispositivo principal da intervenção",
    )
    grounding = SymbolPlacement(
        symbol_type="GROUND_AT",
        position=Point(x=0.5, y=0.5),
        label="Aterramento temporário de alta tensão no poste",
    )

    assert _compact_equipment_label(transformer) == "TR 900001 - 112,5 kVA"
    assert _compact_symbol_label(grounding) == "AT"
