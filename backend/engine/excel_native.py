from __future__ import annotations

import copy
import posixpath
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile

from .models import CroquiPlan, Point, ProjectMetadata, Segment, WorkZone

NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_REL_DOC = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_REL_PACKAGE = "http://schemas.openxmlformats.org/package/2006/relationships"
NS_XDR = "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
NS_R = NS_REL_DOC
NS_CP = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
NS_DC = "http://purl.org/dc/elements/1.1/"

for prefix, uri in (
    ("", NS_MAIN),
    ("xdr", NS_XDR),
    ("a", NS_A),
    ("r", NS_R),
    ("cp", NS_CP),
    ("dc", NS_DC),
):
    ET.register_namespace(prefix, uri)


CANONICAL_SYMBOLS: dict[str, str] = {
    "TR": "AutoShape 238",
    "FU": "Group 729",
    "FC": "Group 319",
    "RL": "Text Box 245",
    "RG": "Group 260",
    "OL": "Text Box 261",
    "SC": "Text Box 247",
    # O poste oficial é o grupo com dois círculos concêntricos da linha
    # "Poste existente". Oval 148 é apenas uma elipse auxiliar da mesma área
    # da planilha e não representa a simbologia usada nos croquis finais.
    "POLE": "Group 184",
    "WORK_ZONE": "Rectangle 334",
    "LINE_SECONDARY": "Line 429",
    "LINE_PRIMARY": "Line 430",
    "LINE_PROJECTED": "Line 431",
    "POLE_NEW": "Group 156",
    "CROSS_CONNECTED": "Group 768",
    "CROSS_DISCONNECTED": "Group 183",
    "PASSAGE_PRIMARY": "Group 188",
    "PASSAGE_SECONDARY": "Group 193",
    "PASSAGE_DUAL": "Group 200",
    "PRIMARY_GAUGE_CHANGE": "Group 792",
    "SECONDARY_GAUGE_CHANGE": "Group 794",
    "SECTION_PRIMARY": "Group 225",
    "SECTION_SECONDARY": "Group 226",
    "TR_PRIVATE": "AutoShape 243",
    "CAPACITOR": "Group 254",
    "FUSE_REPEATER": "Group 698",
    "FUSE_NO_LOAD_BREAK": "Group 718",
    "KNIFE_NO_LOAD_BREAK": "Group 302",
    "KNIFE_LOAD_BREAK": "Group 319",
    "KNIFE_TRIPOLAR_NO_LOAD_BREAK": "Group 321",
    "KNIFE_TRIPOLAR_LOAD_BREAK": "Group 332",
    "OMNI_RUPTER": "Group 764",
    "GROUND_BT": "Group 72",
    "GROUND_AT": "Group 80",
    "WORK_ZONE_OVAL": "Oval 26855",
}


class TemplateError(RuntimeError):
    pass


def _q(namespace: str, tag: str) -> str:
    return f"{{{namespace}}}{tag}"


def _resolve(base: str, target: str) -> str:
    return posixpath.normpath(str(PurePosixPath(base).parent.joinpath(target)))


def _relationships(payload: bytes) -> dict[str, str]:
    root = ET.fromstring(payload)
    return {
        relationship.attrib["Id"]: relationship.attrib["Target"]
        for relationship in root.findall(_q(NS_REL_PACKAGE, "Relationship"))
    }


@dataclass(frozen=True)
class WorkbookParts:
    croqui_sheet: str
    croqui_drawing: str
    symbol_drawing: str


def _workbook_parts(archive: ZipFile) -> WorkbookParts:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    workbook_rels = _relationships(archive.read("xl/_rels/workbook.xml.rels"))
    sheets: dict[str, str] = {}
    for sheet in workbook.findall(f".//{_q(NS_MAIN, 'sheet')}"):
        rel_id = sheet.attrib[_q(NS_REL_DOC, "id")]
        sheets[sheet.attrib["name"].casefold()] = _resolve("xl/workbook.xml", workbook_rels[rel_id])

    def by_name(fragment: str) -> str:
        matches = [target for name, target in sheets.items() if fragment in name]
        if len(matches) != 1:
            raise TemplateError(f"o modelo precisa ter uma única aba contendo '{fragment}'")
        return matches[0]

    croqui_sheet = by_name("croqui")
    symbol_sheet = by_name("simbologia")

    def drawing_for(sheet_path: str) -> str:
        rel_path = str(PurePosixPath(sheet_path).parent / "_rels" / f"{PurePosixPath(sheet_path).name}.rels")
        if rel_path not in archive.namelist():
            raise TemplateError(f"a aba {sheet_path} não possui objetos de desenho")
        rels = _relationships(archive.read(rel_path))
        targets = [target for target in rels.values() if "drawing" in target]
        if len(targets) != 1:
            raise TemplateError(f"a aba {sheet_path} deve apontar para um único drawing")
        return _resolve(sheet_path, targets[0])

    return WorkbookParts(
        croqui_sheet=croqui_sheet,
        croqui_drawing=drawing_for(croqui_sheet),
        symbol_drawing=drawing_for(symbol_sheet),
    )


def _top_name(anchor: ET.Element) -> str:
    prop = anchor.find(f".//{_q(NS_XDR, 'cNvPr')}")
    return prop.attrib.get("name", "") if prop is not None else ""


def _symbol_anchors(symbol_drawing: bytes) -> dict[str, ET.Element]:
    root = ET.fromstring(symbol_drawing)
    by_name: dict[str, list[ET.Element]] = {}
    for anchor in list(root):
        by_name.setdefault(_top_name(anchor), []).append(anchor)
    catalog: dict[str, ET.Element] = {}
    missing: list[str] = []
    for key, official_name in CANONICAL_SYMBOLS.items():
        matches = by_name.get(official_name, [])
        if not matches:
            missing.append(f"{key}={official_name}")
            continue
        # Alguns arquivos históricos repetem o nome do TR na seção de cores.
        # A primeira ocorrência é o objeto oficial preto da tabela de símbolos.
        catalog[key] = matches[0]
    if missing:
        raise TemplateError("símbolos oficiais ausentes: " + ", ".join(missing))
    return catalog


def inspect_template(path: Path) -> dict[str, str]:
    with ZipFile(path) as archive:
        parts = _workbook_parts(archive)
        _symbol_anchors(archive.read(parts.symbol_drawing))
        return {
            "croqui_sheet": parts.croqui_sheet,
            "croqui_drawing": parts.croqui_drawing,
            "symbol_drawing": parts.symbol_drawing,
            **CANONICAL_SYMBOLS,
        }


def _outer_xfrm(anchor: ET.Element) -> ET.Element:
    object_node = next(
        (
            node
            for node in list(anchor)
            if node.tag in {_q(NS_XDR, "sp"), _q(NS_XDR, "grpSp"), _q(NS_XDR, "pic")}
        ),
        None,
    )
    if object_node is None:
        raise TemplateError(f"objeto {_top_name(anchor)} não possui shape DrawingML")
    direct_candidates = (
        f"./{_q(NS_XDR, 'spPr')}/{_q(NS_A, 'xfrm')}",
        f"./{_q(NS_XDR, 'grpSpPr')}/{_q(NS_A, 'xfrm')}",
    )
    for path in direct_candidates:
        found = object_node.find(path)
        if found is not None:
            return found
    raise TemplateError(f"objeto {_top_name(anchor)} não possui transformação")


def _offset_and_extent(anchor: ET.Element) -> tuple[int, int, int, int]:
    transform = _outer_xfrm(anchor)
    offset = transform.find(_q(NS_A, "off"))
    extent = transform.find(_q(NS_A, "ext"))
    if offset is None or extent is None:
        raise TemplateError(f"objeto {_top_name(anchor)} sem posição ou tamanho")
    return (
        int(offset.attrib["x"]),
        int(offset.attrib["y"]),
        int(extent.attrib["cx"]),
        int(extent.attrib["cy"]),
    )


CANVAS_LEFT = 420_000
CANVAS_TOP = 1_270_000
CANVAS_WIDTH = 8_250_000
CANVAS_HEIGHT = 3_970_000
# Os modelos oficiais foram convertidos do XLS binário pelo LibreOffice. Nessa
# conversão, a posição dos twoCellAnchor usa a grade da planilha, enquanto a
# extensão xfrm mantém outra escala. Estes fatores reconciliam as duas medidas
# sem alterar o desenho interno dos símbolos clonados.
ANCHOR_EXTENT_SCALE_X = 2.25
ANCHOR_EXTENT_SCALE_Y = 1.55


def _canvas(point: Point) -> tuple[int, int]:
    return (
        int(CANVAS_LEFT + point.x * CANVAS_WIDTH),
        int(CANVAS_TOP + point.y * CANVAS_HEIGHT),
    )


def _shift_anchor(anchor: ET.Element, dx: int, dy: int) -> None:
    # Um grupo DrawingML possui um sistema de coordenadas próprio. Somente o
    # offset da transformação externa deve acompanhar a nova posição; mover os
    # a:off internos ou a:chOff deforma e separa os componentes do símbolo.
    transform = _outer_xfrm(anchor)
    offset = transform.find(_q(NS_A, "off"))
    if offset is None:
        raise TemplateError(f"objeto {_top_name(anchor)} sem posição")
    offset.set("x", str(int(offset.attrib.get("x", "0")) + dx))
    offset.set("y", str(int(offset.attrib.get("y", "0")) + dy))


def _translate_anchor_cells(anchor: ET.Element, dx: int, dy: int) -> None:
    """Move o twoCellAnchor sem alterar sua caixa ou seus offsets oficiais.

    Excel e LibreOffice podem priorizar partes diferentes da geometria do
    DrawingML. Por isso a transformação EMU e a âncora de células precisam ser
    transladadas em conjunto, mantendo exatamente o tamanho do objeto fonte.
    """

    origin = anchor.find(_q(NS_XDR, "from"))
    destination = anchor.find(_q(NS_XDR, "to"))
    if origin is None or destination is None:
        return
    delta_col = round(dx * 42 / CANVAS_WIDTH)
    delta_row = round(dy * 24 / CANVAS_HEIGHT)
    for node in (origin, destination):
        col = node.find(_q(NS_XDR, "col"))
        row = node.find(_q(NS_XDR, "row"))
        if col is None or row is None:
            raise TemplateError(f"objeto {_top_name(anchor)} sem âncora de célula completa")
        col.text = str(int(col.text or "0") + delta_col)
        row.text = str(int(row.text or "0") + delta_row)


def _set_symbol_anchor_box(anchor: ET.Element, point: Point, width: int, height: int) -> None:
    """Preserva o tamanho físico do símbolo, independentemente da grade da aba."""
    start_col = 2 + round(point.x * 42)
    start_row = 7 + round(point.y * 24)
    origin = anchor.find(_q(NS_XDR, "from"))
    destination = anchor.find(_q(NS_XDR, "to"))
    if origin is None or destination is None:
        return
    for node in (origin, destination):
        node.find(_q(NS_XDR, "col")).text = str(start_col)
        node.find(_q(NS_XDR, "row")).text = str(start_row)
    origin.find(_q(NS_XDR, "colOff")).text = "0"
    origin.find(_q(NS_XDR, "rowOff")).text = "0"
    destination.find(_q(NS_XDR, "colOff")).text = str(width)
    destination.find(_q(NS_XDR, "rowOff")).text = str(height)


def _set_anchor_cells(anchor: ET.Element, point: Point, span_col: int = 2, span_row: int = 2) -> None:
    start_col = 2 + round(point.x * 42)
    start_row = 7 + round(point.y * 24)
    origin = anchor.find(_q(NS_XDR, "from"))
    destination = anchor.find(_q(NS_XDR, "to"))
    if origin is None or destination is None:
        return
    for node, col, row in (
        (origin, start_col, start_row),
        (destination, start_col + span_col, start_row + span_row),
    ):
        node.find(_q(NS_XDR, "col")).text = str(col)
        node.find(_q(NS_XDR, "row")).text = str(row)
        node.find(_q(NS_XDR, "colOff")).text = "0"
        node.find(_q(NS_XDR, "rowOff")).text = "0"


def _set_anchor_range(anchor: ET.Element, start: Point, end: Point) -> None:
    origin = anchor.find(_q(NS_XDR, "from"))
    destination = anchor.find(_q(NS_XDR, "to"))
    if origin is None or destination is None:
        return
    left, right = sorted((start.x, end.x))
    top, bottom = sorted((start.y, end.y))
    for node, col, row in (
        (origin, 2 + round(left * 42), 7 + round(top * 24)),
        (destination, 2 + round(right * 42), 7 + round(bottom * 24)),
    ):
        node.find(_q(NS_XDR, "col")).text = str(col)
        node.find(_q(NS_XDR, "row")).text = str(row)
        node.find(_q(NS_XDR, "colOff")).text = "0"
        node.find(_q(NS_XDR, "rowOff")).text = "0"


def _renumber(anchor: ET.Element, next_id: int) -> int:
    for prop in anchor.findall(f".//{_q(NS_XDR, 'cNvPr')}"):
        prop.set("id", str(next_id))
        next_id += 1
    return next_id


def _clone_symbol(source: ET.Element, point: Point, next_id: int) -> tuple[ET.Element, int]:
    anchor = copy.deepcopy(source)
    x, y, width, height = _offset_and_extent(anchor)
    target_x, target_y = _canvas(point)
    dx = target_x - (x + width // 2)
    dy = target_y - (y + height // 2)
    _shift_anchor(anchor, dx, dy)
    _set_symbol_anchor_box(anchor, point, width, height)
    return anchor, _renumber(anchor, next_id)


def _set_line_geometry(anchor: ET.Element, segment: Segment) -> None:
    start_x, start_y = _canvas(segment.start)
    end_x, end_y = _canvas(segment.end)
    transform = _outer_xfrm(anchor)
    offset = transform.find(_q(NS_A, "off"))
    extent = transform.find(_q(NS_A, "ext"))
    offset.set("x", str(min(start_x, end_x)))
    offset.set("y", str(min(start_y, end_y)))
    extent.set("cx", str(max(round(abs(end_x - start_x) * ANCHOR_EXTENT_SCALE_X), 1)))
    extent.set("cy", str(max(round(abs(end_y - start_y) * ANCHOR_EXTENT_SCALE_Y), 1)))
    for attribute in ("flipH", "flipV"):
        transform.attrib.pop(attribute, None)
    if end_x < start_x:
        transform.set("flipH", "1")
    if end_y < start_y:
        transform.set("flipV", "1")
    _set_anchor_range(anchor, segment.start, segment.end)


def _clone_line(source: ET.Element, segment: Segment, next_id: int) -> tuple[ET.Element, int]:
    anchor = copy.deepcopy(source)
    _set_line_geometry(anchor, segment)
    return anchor, _renumber(anchor, next_id)


def _clone_zone(source: ET.Element, zone: WorkZone, next_id: int) -> tuple[ET.Element, int]:
    anchor = copy.deepcopy(source)
    x1, y1 = _canvas(zone.top_left)
    x2, y2 = _canvas(zone.bottom_right)
    transform = _outer_xfrm(anchor)
    transform.attrib.pop("rot", None)
    offset = transform.find(_q(NS_A, "off"))
    extent = transform.find(_q(NS_A, "ext"))
    offset.set("x", str(x1))
    offset.set("y", str(y1))
    extent.set("cx", str(max(round((x2 - x1) * ANCHOR_EXTENT_SCALE_X), 1)))
    extent.set("cy", str(max(round((y2 - y1) * ANCHOR_EXTENT_SCALE_Y), 1)))
    _set_anchor_range(anchor, zone.top_left, zone.bottom_right)
    return anchor, _renumber(anchor, next_id)


def _label_anchor(
    text: str,
    point: Point,
    next_id: int,
    equipment_type: str,
) -> tuple[ET.Element, int]:
    x, y = _canvas(point)
    offset_x, offset_y = {
        "FU": (-310_000, -355_000),
        "FC": (-310_000, -330_000),
        "RG": (250_000, -270_000),
        "TR": (150_000, -190_000),
    }.get(equipment_type, (150_000, -230_000))
    x += offset_x
    y += offset_y
    anchor_dx, anchor_dy = {
        "FU": (-0.035, -0.065),
        "FC": (-0.035, -0.06),
        "TR": (0.018, 0.04),
        "RG": (0.025, -0.04),
    }.get(equipment_type, (0.018, -0.045))
    label_point = Point(
        x=min(max(point.x + anchor_dx, 0), 1),
        y=min(max(point.y + anchor_dy, 0), 1),
    )
    width, height = max(620_000, len(text) * 79_000), 220_000
    xml = f"""
    <xdr:twoCellAnchor xmlns:xdr="{NS_XDR}" xmlns:a="{NS_A}" editAs="oneCell">
      <xdr:from><xdr:col>2</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>7</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from>
      <xdr:to><xdr:col>8</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>9</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:to>
      <xdr:sp>
        <xdr:nvSpPr><xdr:cNvPr id="{next_id}" name="Jobel Label {next_id}"/><xdr:cNvSpPr txBox="1"/></xdr:nvSpPr>
        <xdr:spPr><a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{width}" cy="{height}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/><a:ln><a:noFill/></a:ln></xdr:spPr>
        <xdr:txBody><a:bodyPr wrap="none"/><a:lstStyle/><a:p><a:r><a:rPr lang="pt-BR" sz="900" b="1"><a:latin typeface="Arial"/></a:rPr><a:t>{_xml_escape(text)}</a:t></a:r><a:endParaRPr lang="pt-BR" sz="900"/></a:p></xdr:txBody>
      </xdr:sp>
      <xdr:clientData/>
    </xdr:twoCellAnchor>"""
    anchor = ET.fromstring(xml)
    _set_anchor_cells(anchor, label_point, span_col=6, span_row=2)
    return anchor, next_id + 1


def _xml_escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _compact_equipment_label(item) -> str:
    base = f"{item.equipment_type} {item.number}"
    if str(item.equipment_type) != "TR":
        return base
    power = re.search(r"\b\d+(?:[.,]\d+)?\s*KVA\b", item.label, re.IGNORECASE)
    return f"{base} - {power.group(0)}" if power else base


def _compact_symbol_label(item) -> str:
    key = str(item.symbol_type)
    if key == "GROUND_BT":
        return "BT"
    if key == "GROUND_AT":
        return "AT"
    label = " ".join(item.label.split())
    if not label:
        return ""
    switch_types = {
        "FUSE_REPEATER",
        "FUSE_NO_LOAD_BREAK",
        "KNIFE_NO_LOAD_BREAK",
        "KNIFE_LOAD_BREAK",
        "KNIFE_TRIPOLAR_NO_LOAD_BREAK",
        "KNIFE_TRIPOLAR_LOAD_BREAK",
        "OMNI_RUPTER",
    }
    if key in switch_types:
        number = re.search(r"\b\d{5,8}\b", label)
        return number.group(0) if number else label[:18]
    return label if len(label) <= 24 else ""


def _symbol_label_family(symbol_type: str) -> str:
    if symbol_type.startswith("FUSE_"):
        return "FU"
    if symbol_type.startswith("KNIFE_") or symbol_type == "OMNI_RUPTER":
        return "FC"
    return symbol_type


def _set_inline_cell(sheet: ET.Element, reference: str, value: str) -> None:
    match = re.fullmatch(r"([A-Z]+)(\d+)", reference)
    if not match:
        raise ValueError(reference)
    row_number = int(match.group(2))
    sheet_data = sheet.find(_q(NS_MAIN, "sheetData"))
    if sheet_data is None:
        sheet_data = ET.SubElement(sheet, _q(NS_MAIN, "sheetData"))
    row = next(
        (item for item in sheet_data.findall(_q(NS_MAIN, "row")) if item.attrib.get("r") == str(row_number)),
        None,
    )
    if row is None:
        row = ET.SubElement(sheet_data, _q(NS_MAIN, "row"), {"r": str(row_number)})
    cell = next((item for item in row.findall(_q(NS_MAIN, "c")) if item.attrib.get("r") == reference), None)
    if cell is None:
        cell = ET.SubElement(row, _q(NS_MAIN, "c"), {"r": reference})
    for child in list(cell):
        cell.remove(child)
    cell.set("t", "inlineStr")
    inline = ET.SubElement(cell, _q(NS_MAIN, "is"))
    text = ET.SubElement(inline, _q(NS_MAIN, "t"))
    text.text = value


def _update_sheet(payload: bytes, metadata: ProjectMetadata, equipment_label: str) -> bytes:
    root = ET.fromstring(payload)
    values = {
        "I5": metadata.departamento or "SERRA",
        "AA5": metadata.municipio,
        "AP5": equipment_label,
        "I6": metadata.data_projeto,
        "AH6": metadata.levantador,
    }
    for reference, value in values.items():
        _set_inline_cell(root, reference, value)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


OFFICIAL_HEADER_CELLS = {"I5", "AA5", "AP5", "I6", "AH6"}
OFFICIAL_VIABILITY_CELLS = {f"AQ{row}" for row in range(33, 43)}


def _shared_string_references(sheet_payload: bytes, only: set[str] | None = None) -> set[int]:
    root = ET.fromstring(sheet_payload)
    result: set[int] = set()
    for cell in root.findall(f".//{_q(NS_MAIN, 'c')}"):
        if cell.attrib.get("t") != "s":
            continue
        if only is not None and cell.attrib.get("r") not in only:
            continue
        value = cell.find(_q(NS_MAIN, "v"))
        if value is not None and value.text is not None:
            result.add(int(value.text))
    return result


def _remove_unused_header_strings(payloads: dict[str, bytes], header_indices: set[int]) -> None:
    shared_path = "xl/sharedStrings.xml"
    if not header_indices or shared_path not in payloads:
        return
    still_used: set[int] = set()
    for path, payload in payloads.items():
        if path.startswith("xl/worksheets/") and path.endswith(".xml"):
            still_used.update(_shared_string_references(payload))
    root = ET.fromstring(payloads[shared_path])
    strings = root.findall(_q(NS_MAIN, "si"))
    for index in header_indices - still_used:
        if index >= len(strings):
            continue
        item = strings[index]
        for child in list(item):
            item.remove(child)
        ET.SubElement(item, _q(NS_MAIN, "t")).text = ""
    payloads[shared_path] = ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _sanitize_core_properties(payload: bytes) -> bytes:
    root = ET.fromstring(payload)
    replacements = {
        _q(NS_DC, "creator"): "JOBEL Engenharia",
        _q(NS_CP, "lastModifiedBy"): "JOBEL Engenharia",
        _q(NS_DC, "title"): "Modelo oficial de croquis",
    }
    for tag, value in replacements.items():
        node = root.find(tag)
        if node is None:
            node = ET.SubElement(root, tag)
        node.text = value
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _set_formula_cell(sheet: ET.Element, reference: str, formula: str) -> None:
    match = re.fullmatch(r"([A-Z]+)(\d+)", reference)
    if not match:
        raise ValueError(reference)
    row_number = match.group(2)
    row = next(
        (item for item in sheet.findall(f".//{_q(NS_MAIN, 'row')}") if item.attrib.get("r") == row_number),
        None,
    )
    if row is None:
        raise TemplateError(f"célula oficial ausente: {reference}")
    cell = next((item for item in row.findall(_q(NS_MAIN, "c")) if item.attrib.get("r") == reference), None)
    if cell is None:
        raise TemplateError(f"célula oficial ausente: {reference}")
    for child in list(cell):
        cell.remove(child)
    cell.attrib.pop("t", None)
    ET.SubElement(cell, _q(NS_MAIN, "f")).text = formula


def _sanitize_viability(payload: bytes) -> bytes:
    root = ET.fromstring(payload)
    for row in range(33, 43):
        _set_inline_cell(root, f"AQ{row}", "")
        _set_formula_cell(root, f"AW{row}", f'IF(AQ{row}="","",IF(AQ{row}="Sim",1,0))')
    _set_formula_cell(root, "AU32", 'IF(COUNT(AW33:AW40)=0,"",AVERAGE(AW33:AW40))')
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def create_official_template(source: Path, output: Path) -> Path:
    """Cria o asset interno sem diagrama ou dados de uma obra específica.

    A aba Simbologia não é reconstruída: seu DrawingML é copiado byte a byte.
    Na aba Croqui ficam apenas o desenho do formulário e as imagens do
    cabeçalho, incluindo o logo RGE.
    """

    with ZipFile(source, "r") as archive:
        parts = _workbook_parts(archive)
        symbol_payload = archive.read(parts.symbol_drawing)
        _symbol_anchors(symbol_payload)
        payloads = {entry.filename: archive.read(entry.filename) for entry in archive.infolist()}
        private_indices = _shared_string_references(
            payloads[parts.croqui_sheet],
            OFFICIAL_HEADER_CELLS | OFFICIAL_VIABILITY_CELLS,
        )

        croqui_root = ET.fromstring(payloads[parts.croqui_drawing])
        pictures = [
            copy.deepcopy(anchor)
            for anchor in list(croqui_root)
            if anchor.find(f".//{_q(NS_XDR, 'pic')}") is not None
        ]
        if not pictures:
            raise TemplateError("o modelo de origem não contém o logo oficial no cabeçalho")
        for anchor in list(croqui_root):
            croqui_root.remove(anchor)
        for picture in pictures:
            croqui_root.append(picture)
        payloads[parts.croqui_drawing] = ET.tostring(
            croqui_root,
            encoding="utf-8",
            xml_declaration=True,
        )
        payloads[parts.croqui_sheet] = _update_sheet(
            payloads[parts.croqui_sheet],
            ProjectMetadata(departamento="SERRA"),
            "",
        )
        payloads[parts.croqui_sheet] = _sanitize_viability(payloads[parts.croqui_sheet])
        _remove_unused_header_strings(payloads, private_indices)
        if "docProps/core.xml" in payloads:
            payloads["docProps/core.xml"] = _sanitize_core_properties(payloads["docProps/core.xml"])

        output.parent.mkdir(parents=True, exist_ok=True)
        with ZipFile(output, "w", ZIP_DEFLATED) as target:
            for entry in archive.infolist():
                target.writestr(entry, payloads[entry.filename])

    inspect_template(output)
    with ZipFile(output) as archive:
        parts = _workbook_parts(archive)
        if archive.read(parts.symbol_drawing) != symbol_payload:
            raise TemplateError("a aba Simbologia foi alterada durante a sanitização")
        croqui_root = ET.fromstring(archive.read(parts.croqui_drawing))
        if not list(croqui_root) or any(
            anchor.find(f".//{_q(NS_XDR, 'pic')}") is None for anchor in list(croqui_root)
        ):
            raise TemplateError("o modelo interno ainda contém objetos de uma obra")
    return output


def build_native_workbook(template: Path, output: Path, plan: CroquiPlan, metadata: ProjectMetadata) -> Path:
    if plan.main_equipment is None:
        raise TemplateError("não é possível exportar sem equipamento principal")
    with ZipFile(template, "r") as archive:
        parts = _workbook_parts(archive)
        catalog = _symbol_anchors(archive.read(parts.symbol_drawing))
        croqui_root = ET.fromstring(archive.read(parts.croqui_drawing))
        # A identidade visual do cabeçalho permanece exatamente como veio do
        # modelo. Todos os objetos antigos do diagrama são substituídos.
        pictures = [
            copy.deepcopy(anchor)
            for anchor in list(croqui_root)
            if anchor.find(f".//{_q(NS_XDR, 'pic')}") is not None
        ]
        for anchor in list(croqui_root):
            croqui_root.remove(anchor)
        for picture in pictures:
            croqui_root.append(picture)

        next_id = (
            max(
                [int(item.attrib.get("id", "0")) for item in croqui_root.findall(f".//{_q(NS_XDR, 'cNvPr')}")]
                + [0]
            )
            + 1
        )
        for segment in plan.segments:
            key = {
                "secondary": "LINE_SECONDARY",
                "projected": "LINE_PROJECTED",
                "work": "LINE_PROJECTED",
            }.get(segment.style, "LINE_PRIMARY")
            anchor, next_id = _clone_line(catalog[key], segment, next_id)
            croqui_root.append(anchor)
        for pole in plan.poles:
            anchor, next_id = _clone_symbol(catalog["POLE"], pole, next_id)
            croqui_root.append(anchor)
        for item in plan.equipment:
            anchor, next_id = _clone_symbol(catalog[str(item.equipment_type)], item.position, next_id)
            croqui_root.append(anchor)
            label = _compact_equipment_label(item)
            anchor, next_id = _label_anchor(label, item.position, next_id, str(item.equipment_type))
            croqui_root.append(anchor)
        for item in plan.symbols:
            key = str(item.symbol_type)
            anchor, next_id = _clone_symbol(catalog[key], item.position, next_id)
            croqui_root.append(anchor)
            label = _compact_symbol_label(item)
            if label:
                anchor, next_id = _label_anchor(
                    label,
                    item.position,
                    next_id,
                    _symbol_label_family(key),
                )
                croqui_root.append(anchor)
        for zone in plan.work_zones:
            anchor, next_id = _clone_zone(catalog["WORK_ZONE"], zone, next_id)
            croqui_root.append(anchor)

        drawing_payload = ET.tostring(croqui_root, encoding="utf-8", xml_declaration=True)
        sheet_payload = _update_sheet(
            archive.read(parts.croqui_sheet),
            metadata,
            f"{plan.main_equipment.equipment_type} {plan.main_equipment.number}",
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        with ZipFile(output, "w", ZIP_DEFLATED) as target:
            for entry in archive.infolist():
                payload = archive.read(entry.filename)
                if entry.filename == parts.croqui_drawing:
                    payload = drawing_payload
                elif entry.filename == parts.croqui_sheet:
                    payload = sheet_payload
                target.writestr(entry, payload)
    validate_native_workbook(output, plan)
    return output


def validate_native_workbook(path: Path, plan: CroquiPlan) -> None:
    with ZipFile(path) as archive:
        parts = _workbook_parts(archive)
        root = ET.fromstring(archive.read(parts.croqui_drawing))
        if root.find(f".//{_q(NS_XDR, 'pic')}") is None:
            raise TemplateError("o logo oficial do cabeçalho foi perdido")
        names = [_top_name(anchor) for anchor in list(root)]
        for item in plan.equipment:
            expected = CANONICAL_SYMBOLS[str(item.equipment_type)]
            if expected not in names:
                raise TemplateError(f"o símbolo oficial {expected} não foi clonado")
        for item in plan.symbols:
            expected = CANONICAL_SYMBOLS[str(item.symbol_type)]
            if expected not in names:
                raise TemplateError(f"o símbolo oficial {expected} não foi clonado")
        line_names = {
            CANONICAL_SYMBOLS["LINE_SECONDARY"],
            CANONICAL_SYMBOLS["LINE_PRIMARY"],
            CANONICAL_SYMBOLS["LINE_PROJECTED"],
        }
        native_lines = sum(name in line_names for name in names)
        if native_lines != len(plan.segments):
            raise TemplateError("a quantidade de linhas nativas diverge do plano")
