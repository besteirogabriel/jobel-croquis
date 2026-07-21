from pathlib import Path

import pytest

from backend.evaluation import _prediction_symbol_counts, _summarize_rows, compare_blind


def test_compare_refuses_to_read_targets_before_blind_phase_finishes(tmp_path: Path):
    with pytest.raises(RuntimeError, match="execução cega"):
        compare_blind(tmp_path)


def test_prediction_signature_counts_exportable_symbols():
    plan = {
        "equipment": [{"equipment_type": "TR"}, {"equipment_type": "FU"}],
        "symbols": [{"symbol_type": "GROUND_BT"}],
        "poles": [{}, {}],
        "segments": [{"style": "primary"}, {"style": "secondary"}],
        "work_zones": [{}],
    }
    assert _prediction_symbol_counts(plan) == {
        "FU_LOAD_BREAK": 1,
        "GROUND_BT": 1,
        "LINE_PRIMARY": 1,
        "LINE_SECONDARY": 1,
        "POLE_EXISTING": 2,
        "TR": 1,
        "WORK_ZONE_RECT": 1,
    }


def test_split_summary_reports_exact_main_and_symbol_metrics():
    summary = _summarize_rows(
        [
            {"main_exact": True, "symbol_precision": 0.8, "symbol_recall": 0.6},
            {"main_exact": False, "symbol_precision": 1.0, "symbol_recall": 0.8},
        ]
    )
    assert summary == {
        "cases": 2,
        "main_accuracy": 0.5,
        "mean_symbol_precision": 0.9,
        "mean_symbol_recall": 0.7,
    }
