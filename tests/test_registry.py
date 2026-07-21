from pathlib import Path

from backend.engine.models import EquipmentCandidate, LocalExtraction, ProjectMetadata
from backend.engine.registry import enrich_from_registry, load_registry


def test_registry_supplies_upstream_isolation(tmp_path: Path):
    registry = tmp_path / "registry.csv"
    registry.write_text(
        "equipamento referencia;tipo isolamento;numero isolamento;municipio\n"
        "800001;FU;900001;Cidade Exemplo\n",
        encoding="utf-8",
    )
    links = load_registry(registry, work_dir=tmp_path, libreoffice_bin="soffice")
    extraction = LocalExtraction(
        metadata=ProjectMetadata(municipio="CIDADE EXEMPLO"),
        identifiers=["800001"],
        candidates=[EquipmentCandidate(equipment_type="TR", number="800001", score=0.79)],
    )
    enrich_from_registry(extraction, links)
    assert extraction.registry_identifiers == ["900001"]
    assert extraction.candidates[0].equipment_type == "FU"
    assert extraction.candidates[0].number == "900001"
    assert extraction.candidates[0].score == 0.96
