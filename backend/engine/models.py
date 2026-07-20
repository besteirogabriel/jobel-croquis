from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field, model_validator


class EquipmentType(StrEnum):
    TR = "TR"
    FU = "FU"
    FC = "FC"
    RL = "RL"
    RG = "RG"
    OL = "OL"
    SC = "SC"


class Point(BaseModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


class Segment(BaseModel):
    start: Point
    end: Point
    style: str = "primary"


class EquipmentCandidate(BaseModel):
    equipment_type: EquipmentType
    number: str = Field(pattern=r"^\d{5,8}$")
    score: float = Field(ge=0, le=1)
    evidence: list[str] = Field(default_factory=list)
    position: Point | None = None


class EquipmentPlacement(BaseModel):
    equipment_type: EquipmentType
    number: str = Field(pattern=r"^\d{5,8}$")
    position: Point
    label: str = ""
    main: bool = False


class WorkZone(BaseModel):
    top_left: Point
    bottom_right: Point

    @model_validator(mode="after")
    def ordered(self) -> WorkZone:
        if self.top_left.x >= self.bottom_right.x or self.top_left.y >= self.bottom_right.y:
            raise ValueError("limites inválidos da zona de trabalho")
        return self


class ProjectMetadata(BaseModel):
    municipio: str = ""
    obra: str = ""
    data_projeto: str = ""
    nota: str = ""
    levantador: str = ""
    departamento: str = ""
    pages: int = 0


class ManeuverAction(BaseModel):
    action: str
    equipment_type: str
    number: str


class LocalExtraction(BaseModel):
    metadata: ProjectMetadata
    text: str = Field(exclude=True, default="")
    identifiers: list[str] = Field(default_factory=list)
    registry_identifiers: list[str] = Field(default_factory=list)
    actions: list[ManeuverAction] = Field(default_factory=list)
    candidates: list[EquipmentCandidate] = Field(default_factory=list)
    segments: list[Segment] = Field(default_factory=list)


class CroquiPlan(BaseModel):
    main_equipment: EquipmentPlacement | None = None
    equipment: list[EquipmentPlacement] = Field(default_factory=list)
    poles: list[Point] = Field(default_factory=list)
    segments: list[Segment] = Field(default_factory=list)
    work_zones: list[WorkZone] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1, default=0)
    source: str = "local"
    rationale: list[str] = Field(default_factory=list)


class ValidationIssue(BaseModel):
    code: str
    message: str
    blocking: bool = True


class ValidationResult(BaseModel):
    accepted: bool
    issues: list[ValidationIssue] = Field(default_factory=list)


class EngineArtifacts(BaseModel):
    report: Path | None = None
    xlsx: Path | None = None
    xls: Path | None = None
    pdf: Path | None = None
    preview: Path | None = None


class EngineResult(BaseModel):
    job_id: str
    status: str
    extraction: LocalExtraction
    plan: CroquiPlan
    validation: ValidationResult
    ai_used: bool = False
    artifacts: EngineArtifacts = Field(default_factory=EngineArtifacts)

    def public_dict(self) -> dict:
        data = self.model_dump(mode="json", exclude={"extraction": {"text"}})
        data["artifacts"] = {
            key: value.name if value is not None else None
            for key, value in self.artifacts.model_dump().items()
        }
        data.update(
            {
                "municipio": self.extraction.metadata.municipio,
                "obra": self.extraction.metadata.obra,
                "data_projeto": self.extraction.metadata.data_projeto,
                "nota": self.extraction.metadata.nota,
                "levantador": self.extraction.metadata.levantador,
                "pages": self.extraction.metadata.pages,
                "identificadores": self.extraction.identifiers,
                "acoes": [
                    {"acao": x.action, "tipo": x.equipment_type, "numero": x.number}
                    for x in self.extraction.actions
                ],
                "tipo_isolamento": (
                    self.plan.main_equipment.equipment_type if self.plan.main_equipment else ""
                ),
                "equipamento_isolamento": (
                    self.plan.main_equipment.number if self.plan.main_equipment else ""
                ),
            }
        )
        return data
