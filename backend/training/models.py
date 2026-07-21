from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

DatasetSplit = Literal["train", "validation", "test"]
LabelStatus = Literal["missing", "draft", "approved", "rejected"]


class DatasetCase(BaseModel):
    case_id: str
    group_key: str
    split: DatasetSplit
    corpus_status: str
    equipment_type: str = ""
    equipment_code: str = ""
    project_pdf: str
    project_sha256: str
    target_croqui_pdf: str | None = None
    target_croqui_sha256: str | None = None
    target_croqui_xls: str
    target_croqui_xls_sha256: str
    project_format: str = ""
    project_tokens: list[str] = Field(default_factory=list)
    municipality_hint: str = ""
    label_status: LabelStatus = "missing"
    label_path: str | None = None
    warnings: list[str] = Field(default_factory=list)


class DatasetManifest(BaseModel):
    schema_version: str = "1.0-jobel-croquis"
    source_path: str
    corpus_fingerprint: str
    seed: str
    ratios: dict[DatasetSplit, float]
    cases: list[DatasetCase] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    equipment_type_counts: dict[str, int] = Field(default_factory=dict)
    split_equipment_counts: dict[str, dict[str, int]] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)

    def case_map(self) -> dict[str, DatasetCase]:
        return {case.case_id: case for case in self.cases}


class TrainingLabel(BaseModel):
    schema_version: str = "1.0-jobel-croquis-label"
    case_id: str
    status: LabelStatus = "draft"
    plan: dict[str, Any]
    source_response_id: str = ""
    source_model: str = ""
    reviewer: str = ""
    review_notes: str = ""
    warnings: list[str] = Field(default_factory=list)


class FineTuningBuildReport(BaseModel):
    manifest_fingerprint: str
    training_file: str
    validation_file: str
    evaluation_file: str
    counts: dict[str, int] = Field(default_factory=dict)
    skipped: list[dict[str, str]] = Field(default_factory=list)
