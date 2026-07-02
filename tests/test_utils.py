"""Offline unit tests for utils.py validation helpers."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils import (  # noqa: E402
    filter_dataflows_by_keywords,
    validate_dataflow_id,
    validate_period,
    validate_sdmx_key,
)


def test_validate_dataflow_id_accepts_valid_ids():
    assert validate_dataflow_id("DF_03GARD_PUB")
    assert validate_dataflow_id("DF_05DIS_298_GRP")
    assert validate_dataflow_id("A")


def test_validate_dataflow_id_rejects_invalid_ids():
    assert not validate_dataflow_id("")
    assert not validate_dataflow_id("1_STARTS_WITH_DIGIT")
    assert not validate_dataflow_id("HAS SPACE")


def test_validate_sdmx_key_accepts_valid_keys():
    assert validate_sdmx_key("all")
    assert validate_sdmx_key("A..TH.")
    assert validate_sdmx_key("A+M.TH+BKK.POP")
    assert validate_sdmx_key("...")


def test_validate_sdmx_key_rejects_invalid_keys():
    assert not validate_sdmx_key("A B")
    assert not validate_sdmx_key("A,,B")


def test_validate_period_accepts_valid_periods():
    assert validate_period("2023")
    assert validate_period("2023-01")
    assert validate_period("2023-01-15")
    assert validate_period("2023-Q1")
    assert validate_period("2023-M01")


def test_validate_period_rejects_invalid_periods():
    assert not validate_period("")
    assert not validate_period("2023-13")
    assert not validate_period("not-a-period")


def test_filter_dataflows_by_keywords_scores_and_sorts():
    dataflows = [
        {"id": "DF_POP", "name": "Population Statistics", "description": "By province"},
        {"id": "DF_EDU", "name": "Education Enrollment", "description": "By school level"},
        {"id": "DF_MIX", "name": "Population and Education Mix", "description": "Combined"},
    ]
    result = filter_dataflows_by_keywords(dataflows, ["population", "education"])
    ids = [d["id"] for d in result]
    assert ids[0] == "DF_MIX"  # matches both keywords, should rank first
    assert "DF_POP" in ids
    assert "DF_EDU" in ids


def test_filter_dataflows_by_keywords_empty_keywords_returns_input():
    dataflows = [{"id": "DF_A", "name": "A", "description": ""}]
    assert filter_dataflows_by_keywords(dataflows, []) == dataflows
