import json
import re
from pathlib import Path

from backend.engine.models import CroquiPlan, EngineResult, LocalExtraction, ProjectMetadata, ValidationResult

FORBIDDEN_PUBLIC_RE = re.compile(
    r"\bIA\b|intelig[eê]ncia artificial|openai|chatgpt|codex|provider|prompt|token|"
    r"modelo\s+(?:computacional|de linguagem)|provedor",
    re.IGNORECASE,
)


def _public_text(value) -> str:
    return json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value


def assert_public_payload_is_private(payload) -> None:
    assert FORBIDDEN_PUBLIC_RE.search(_public_text(payload)) is None


def test_public_result_does_not_disclose_ai_backend():
    result = EngineResult(
        job_id="0123456789ab",
        status="NEEDS_REVIEW",
        extraction=LocalExtraction(metadata=ProjectMetadata()),
        plan=CroquiPlan(),
        validation=ValidationResult(accepted=False),
        ai_used=True,
    )
    payload = result.public_dict()
    assert "ai_used" not in payload
    assert_public_payload_is_private(payload)


def test_frontend_does_not_disclose_backend_mechanism():
    frontend = Path("frontend")
    public_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in [frontend / "index.html", frontend / "assets" / "app.js"]
    )
    assert_public_payload_is_private(public_text)


def test_downloadable_report_payload_does_not_disclose_backend_mechanism(tmp_path: Path):
    result = EngineResult(
        job_id="0123456789ab",
        status="NEEDS_REVIEW",
        extraction=LocalExtraction(metadata=ProjectMetadata()),
        plan=CroquiPlan(),
        validation=ValidationResult(accepted=False),
        ai_used=True,
    )
    report = result.public_dict()
    (tmp_path / "relatorio.json").write_text(
        json.dumps(report, ensure_ascii=False),
        encoding="utf-8",
    )

    assert_public_payload_is_private(json.loads((tmp_path / "relatorio.json").read_text()))
