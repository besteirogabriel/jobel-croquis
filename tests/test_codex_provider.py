from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.engine.ai_fallback import (
    AIConfigurationError,
    CodexPlanFallback,
    _strict_schema,
)
from backend.engine.models import (
    CroquiPlan,
    EquipmentPlacement,
    LocalExtraction,
    Point,
    ProjectMetadata,
    Segment,
)
from backend.engine.service import CroquiEngine


def _extraction() -> LocalExtraction:
    return LocalExtraction(
        metadata=ProjectMetadata(pages=1),
        text="TR 900001",
        identifiers=["900001"],
    )


def _plan() -> CroquiPlan:
    main = EquipmentPlacement(
        equipment_type="TR",
        number="900001",
        position=Point(x=0.5, y=0.5),
        label="TR 900001",
        main=True,
    )
    return CroquiPlan(
        main_equipment=main,
        equipment=[main],
        poles=[],
        segments=[
            Segment(start=Point(x=0.2, y=0.5), end=Point(x=0.8, y=0.5))
        ],
        work_zones=[],
        confidence=0.95,
        source="codex",
        rationale=[],
    )


def _provider() -> CodexPlanFallback:
    return CodexPlanFallback(
        binary="codex",
        model="gpt-5.6-sol",
        reasoning_effort="high",
        timeout=30,
        max_project_pages=4,
    )


def test_codex_provider_uses_chatgpt_auth_and_structured_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    project = tmp_path / "projeto.pdf"
    project.write_bytes(b"pdf")
    image = tmp_path / "projeto-1.jpg"
    image.write_bytes(b"jpg")
    captured: dict = {}

    monkeypatch.setattr("backend.engine.ai_fallback.shutil.which", lambda _: "/usr/bin/codex")
    monkeypatch.setattr(
        "backend.engine.ai_fallback.render_pdf_images", lambda *_, **__: [image]
    )
    monkeypatch.setattr(
        "backend.engine.ai_fallback.render_identifier_crops", lambda *_, **__: []
    )
    monkeypatch.setattr(
        "backend.engine.ai_fallback.retrieve_reference_cases", lambda *_, **__: []
    )

    def fake_run(command, **kwargs):
        captured.update(command=command, **kwargs)
        output = Path(command[command.index("--output-last-message") + 1])
        output.write_text(_plan().model_dump_json(), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("backend.engine.ai_fallback.subprocess.run", fake_run)
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-reach-codex")
    monkeypatch.setenv("CODEX_API_KEY", "must-not-reach-codex")

    plan = _provider().propose(project, _extraction())

    assert plan is not None
    assert plan.main_equipment is not None
    assert plan.main_equipment.number == "900001"
    assert plan.source == "automatic"
    assert 'forced_login_method="chatgpt"' in captured["command"]
    assert 'web_search="disabled"' in captured["command"]
    assert captured["command"].count("--disable") == 5
    assert captured["env"].get("OPENAI_API_KEY") is None
    assert captured["env"].get("CODEX_API_KEY") is None
    assert "projeto_atual" in captured["input"]


def test_codex_provider_reports_missing_login(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    project = tmp_path / "projeto.pdf"
    project.write_bytes(b"pdf")
    image = tmp_path / "projeto-1.jpg"
    image.write_bytes(b"jpg")
    monkeypatch.setattr("backend.engine.ai_fallback.shutil.which", lambda _: "/usr/bin/codex")
    monkeypatch.setattr(
        "backend.engine.ai_fallback.render_pdf_images", lambda *_, **__: [image]
    )
    monkeypatch.setattr(
        "backend.engine.ai_fallback.render_identifier_crops", lambda *_, **__: []
    )
    monkeypatch.setattr(
        "backend.engine.ai_fallback.retrieve_reference_cases", lambda *_, **__: []
    )
    monkeypatch.setattr(
        "backend.engine.ai_fallback.subprocess.run",
        lambda command, **_: subprocess.CompletedProcess(
            command, 1, stdout="", stderr="Not logged in. Please run codex login."
        ),
    )

    with pytest.raises(AIConfigurationError, match="não autenticado"):
        _provider().propose(project, _extraction())


def test_codex_is_selected_without_openai_api_key():
    config = SimpleNamespace(
        ai_enabled=True,
        ai_provider="codex",
        openai_api_key="",
        codex_bin="codex",
        codex_model="gpt-5.6-sol",
        codex_reasoning_effort="high",
        codex_timeout_seconds=600,
        codex_max_project_pages=6,
    )

    engine = CroquiEngine(config)

    assert isinstance(engine.fallback, CodexPlanFallback)


def test_codex_schema_is_strict_for_nested_objects():
    schema = _strict_schema(CroquiPlan.model_json_schema())

    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    for definition in schema["$defs"].values():
        if definition.get("type") == "object":
            assert definition["additionalProperties"] is False
            assert set(definition["required"]) == set(definition["properties"])
    json.dumps(schema)
