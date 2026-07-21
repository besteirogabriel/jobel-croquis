from pathlib import Path

import fitz

from backend.engine.extraction import (
    _apply_action_evidence,
    _extract_actions,
    _extract_segments,
    extract_project,
)
from backend.engine.models import EquipmentCandidate, EquipmentType, ManeuverAction, Point


def test_title_block_metadata_uses_pdf_structure(tmp_path: Path):
    pdf = tmp_path / "project.pdf"
    document = fitz.open()
    page = document.new_page(width=600, height=800)
    page.insert_text((300, 500), "Município: Cidade Exemplo")
    page.insert_text((300, 530), "Obra: Ligação Nova")
    page.insert_text((300, 560), "Data: 01/02/2030")
    page.insert_text((300, 590), "Nota: 900000000001")
    page.insert_text((300, 620), "Levantador: Tecnico Exemplo")
    document.save(pdf)
    document.close()

    extraction = extract_project(pdf, ocr_enabled=False)

    assert extraction.metadata.municipio == "Cidade Exemplo"
    assert extraction.metadata.obra == "Ligação Nova"
    assert extraction.metadata.data_projeto == "01/02/2030"
    assert extraction.metadata.nota == "900000000001"
    assert extraction.metadata.levantador == "Tecnico Exemplo"


def test_maneuver_table_is_deduplicated_without_becoming_the_isolation_answer():
    text = "Abrir | Transformador | 900001\nAbrir Transformador 900001"
    actions = _extract_actions(text)
    assert actions == [
        ManeuverAction(action="Abrir", equipment_type="Transformador", number="900001")
    ]

    document = fitz.open()
    document.new_page()
    candidates = [
        EquipmentCandidate(
            equipment_type=EquipmentType.TR,
            number="900001",
            score=0.5,
            position=Point(x=0.5, y=0.5),
        )
    ]
    ranked = _apply_action_evidence(document, text, candidates, actions)
    assert ranked[0].number == "900001"
    assert ranked[0].score == 0.82
    assert "não é conclusão de isolamento" in " ".join(ranked[0].evidence)


def test_transformer_being_replaced_is_not_assumed_to_be_isolation():
    text = "P1: SUBSTITUIR TR TRIF 45KVA. Abrir Transformador 900002"
    actions = _extract_actions(text)
    candidate = EquipmentCandidate(
        equipment_type=EquipmentType.TR,
        number="900002",
        score=0.88,
    )
    document = fitz.open()
    document.new_page()

    ranked = _apply_action_evidence(document, text, [candidate], actions)

    assert ranked[0].score == 0.64
    assert "alvo da obra" in " ".join(ranked[0].evidence)


def test_vector_extraction_keeps_network_colors_and_discards_frame(tmp_path: Path):
    pdf = tmp_path / "network.pdf"
    document = fitz.open()
    page = document.new_page(width=600, height=400)
    page.draw_line((100, 100), (300, 100), color=(0, 0, 1), width=1.2)
    page.draw_line((100, 120), (300, 120), color=(0, 0.7, 0), width=1.2)
    page.draw_line((50, 50), (550, 50), color=(0, 0, 0), width=1.2)
    document.save(pdf)
    document.close()

    with fitz.open(pdf) as reopened:
        segments = _extract_segments(reopened)

    assert [segment.style for segment in segments] == ["primary", "secondary"]
