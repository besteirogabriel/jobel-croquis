from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.engine.decision import _infer_poles
from backend.engine.models import (
    CroquiPlan,
    EquipmentCandidate,
    EquipmentPlacement,
    LocalExtraction,
    Point,
    ProjectMetadata,
    Segment,
    SymbolPlacement,
)
from backend.engine.service import CroquiEngine


def extraction(score: float = 0.95) -> LocalExtraction:
    return LocalExtraction(
        metadata=ProjectMetadata(pages=1),
        text="TR 900001 75 kVA",
        identifiers=["900001", "900002"],
        candidates=[
            EquipmentCandidate(
                equipment_type="TR",
                number="900001",
                score=score,
                evidence=["evidência de teste"],
                position=Point(x=0.5, y=0.5),
            )
        ],
        segments=[
            Segment(start=Point(x=0.35, y=0.5), end=Point(x=0.49, y=0.5)),
            Segment(start=Point(x=0.51, y=0.5), end=Point(x=0.7, y=0.5)),
        ],
    )


def settings() -> SimpleNamespace:
    return SimpleNamespace(
        ai_enabled=False,
        ai_required=True,
        openai_api_key="",
        openai_model="test",
        openai_timeout_seconds=1,
        local_auto_threshold=0.82,
        assisted_generation_threshold=0.0,
        local_min_gap=0.12,
        libreoffice_bin="soffice",
        local_fast_path_enabled=False,
        local_fast_path_threshold=0.9,
    )


class Fallback:
    def __init__(self, plan: CroquiPlan) -> None:
        self.plan = plan
        self.calls = 0

    def propose(self, pdf_path: Path, local: LocalExtraction) -> CroquiPlan:
        self.calls += 1
        return self.plan


def fallback_plan(number: str = "900001") -> CroquiPlan:
    main = EquipmentPlacement(
        equipment_type="TR",
        number=number,
        position=Point(x=0.5, y=0.5),
        label=f"TR {number}",
        main=True,
    )
    return CroquiPlan(
        main_equipment=main,
        equipment=[main],
        segments=[
            Segment(start=Point(x=0.2, y=0.5), end=Point(x=0.45, y=0.5)),
            Segment(start=Point(x=0.55, y=0.5), end=Point(x=0.8, y=0.5)),
        ],
        confidence=0.96,
        source="openai_fallback",
    )


def fallback_plan_without_main_in_equipment() -> CroquiPlan:
    plan = fallback_plan()
    plan.equipment = []
    return plan


def run_with(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, local: LocalExtraction, fallback: Fallback):
    monkeypatch.setattr("backend.engine.service.extract_project", lambda _, **__: local)
    engine = CroquiEngine(settings(), fallback=fallback)
    monkeypatch.setattr(engine, "_export", lambda *_: None)
    return engine.run(
        job_id="0123456789ab",
        project=tmp_path / "project.pdf",
        template=Path("backend/assets/modelo_croqui_oficial.xlsx"),
        job_dir=tmp_path,
    )


def test_strong_local_result_still_requires_complete_analysis(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    fallback = Fallback(fallback_plan())
    result = run_with(monkeypatch, tmp_path, extraction(), fallback)
    assert result.validation.accepted is True
    assert result.plan.source == "openai_fallback"
    assert result.ai_used is True
    assert fallback.calls == 1


def test_blocked_local_result_calls_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    fallback = Fallback(fallback_plan())
    result = run_with(monkeypatch, tmp_path, extraction(score=0.55), fallback)
    assert fallback.calls == 1
    assert result.ai_used is True
    assert result.validation.accepted is True
    assert result.plan.source == "openai_fallback"


def test_fallback_main_equipment_is_normalized_into_export_list(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    fallback = Fallback(fallback_plan_without_main_in_equipment())
    result = run_with(monkeypatch, tmp_path, extraction(score=0.55), fallback)

    assert result.validation.accepted is True
    assert len(result.plan.equipment) == 1
    assert result.plan.equipment[0] == result.plan.main_equipment
    assert result.plan.equipment[0].main is True


def test_scene_normalization_removes_composites_and_adds_missing_vertices():
    plan = fallback_plan()
    plan.symbols = [
        SymbolPlacement(symbol_type="PASSAGE_PRIMARY", position=Point(x=0.3, y=0.5)),
        SymbolPlacement(symbol_type="GROUND_AT", position=Point(x=0.6, y=0.5)),
    ]
    plan.poles = []

    CroquiEngine._normalize_scene_primitives(plan, extraction())

    assert [str(item.symbol_type) for item in plan.symbols] == ["GROUND_AT"]
    assert [(point.x, point.y) for point in plan.poles] == [(0.2, 0.5), (0.8, 0.5)]


def test_weak_local_result_calls_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    fallback = Fallback(fallback_plan())
    result = run_with(monkeypatch, tmp_path, extraction(score=0.84), fallback)
    assert fallback.calls == 1
    assert result.ai_used is True
    assert result.validation.accepted is True
    assert result.plan.source == "openai_fallback"


def test_intermediate_confidence_generates_for_technical_review(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    proposal = fallback_plan()
    proposal.confidence = 0.72
    result = run_with(monkeypatch, tmp_path, extraction(score=0.55), Fallback(proposal))

    assert result.validation.accepted is True
    issue = next(item for item in result.validation.issues if item.code == "CONFIDENCE_REVIEW")
    assert issue.blocking is False


def test_low_confidence_alone_does_not_block_assisted_generation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    proposal = fallback_plan()
    proposal.confidence = 0.05
    result = run_with(monkeypatch, tmp_path, extraction(score=0.55), Fallback(proposal))

    assert result.validation.accepted is True
    issue = next(item for item in result.validation.issues if item.code == "CONFIDENCE_REVIEW")
    assert issue.blocking is False


def test_hallucinated_fallback_is_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    fallback = Fallback(fallback_plan(number="9999999"))
    result = run_with(monkeypatch, tmp_path, extraction(score=0.55), fallback)
    assert fallback.calls == 1
    assert result.validation.accepted is False
    assert result.plan.source == "openai_fallback"
    assert any(issue.code == "MAIN_EQUIPMENT_NOT_IN_PROJECT" for issue in result.validation.issues)


def test_manual_confirmation_preserves_analyzed_scene(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    local = extraction(score=0.55)
    analyzed = fallback_plan()
    analyzed.symbols = [
        SymbolPlacement(symbol_type="GROUND_AT", position=Point(x=0.6, y=0.55))
    ]
    analyzed.poles = [Point(x=0.2, y=0.5), Point(x=0.8, y=0.5)]
    fallback = Fallback(analyzed)
    run_with(monkeypatch, tmp_path, local, fallback)

    engine = CroquiEngine(settings(), fallback=fallback)
    monkeypatch.setattr(engine, "_extract", lambda *_: local)
    monkeypatch.setattr(engine, "_export", lambda *_: None)
    result = engine.override(
        job_id="0123456789ab",
        project=tmp_path / "project.pdf",
        template=Path("backend/assets/modelo_croqui_oficial.xlsx"),
        job_dir=tmp_path,
        equipment_type="FU",
        number="900002",
    )

    assert result.plan.main_equipment.number == "900002"
    assert result.plan.segments == analyzed.segments
    assert result.plan.symbols == analyzed.symbols
    assert result.plan.poles == analyzed.poles
    assert len(result.plan.work_zones) == 1


def test_invalid_fallback_does_not_publish_accepted_local_geometry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    fallback = Fallback(fallback_plan(number="9999999"))
    local_settings = settings()
    local_settings.local_fast_path_enabled = False
    monkeypatch.setattr("backend.engine.service.extract_project", lambda _, **__: extraction(score=0.95))
    engine = CroquiEngine(local_settings, fallback=fallback)
    monkeypatch.setattr(engine, "_export", lambda *_: None)
    result = engine.run(
        job_id="0123456789ab",
        project=tmp_path / "project.pdf",
        template=Path("backend/assets/modelo_croqui_oficial.xlsx"),
        job_dir=tmp_path,
    )
    assert fallback.calls == 1
    assert result.validation.accepted is False
    assert result.plan.source == "openai_fallback"
    assert result.plan.main_equipment.number == "9999999"


def test_poles_follow_dominant_network_vertices():
    segments = [
        Segment(start=Point(x=0.1, y=0.4), end=Point(x=0.4, y=0.4), style="secondary"),
        Segment(start=Point(x=0.4, y=0.4), end=Point(x=0.8, y=0.4), style="secondary"),
        Segment(start=Point(x=0.1, y=0.42), end=Point(x=0.8, y=0.42), style="primary"),
    ]

    poles = _infer_poles(segments)

    assert len(poles) == 3
