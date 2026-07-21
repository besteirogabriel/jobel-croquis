from backend.engine.models import CroquiPlan, EngineResult, LocalExtraction, ProjectMetadata, ValidationResult


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
