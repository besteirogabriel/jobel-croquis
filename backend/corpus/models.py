from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CorpusFile(BaseModel):
    path: str
    name: str
    kind: Literal["PROJECT_PDF", "TARGET_CROQUI_PDF", "TARGET_CROQUI_XLS", "OTHER"]
    size_bytes: int
    sha256: str


class GoldenCase(BaseModel):
    case_id: str
    directory: str
    project_pdfs: list[CorpusFile] = Field(default_factory=list)
    target_croqui_pdfs: list[CorpusFile] = Field(default_factory=list)
    target_croqui_xls: CorpusFile | None = None
    equipment_type: str = ""
    equipment_code: str = ""
    status: Literal[
        "COMPLETE", "MISSING_TARGET_PDF", "MISSING_PROJECT", "MISSING_XLS", "INVALID"
    ] = "INVALID"
    warnings: list[str] = Field(default_factory=list)


class CorpusRegistry(BaseModel):
    source_path: str
    cases: list[GoldenCase] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    equipment_type_counts: dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
