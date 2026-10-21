import pytest

from quantview.ai.workflow import _validate_output


def test_unknown_evidence_reference_is_rejected():
    with pytest.raises(ValueError, match="unknown evidence"):
        _validate_output(
            {
                "bias": "bullish",
                "confidence": 0.8,
                "thesis": "test",
                "evidence_ids": ["invented"],
            },
            {"C-trend"},
        )


def test_confidence_is_bounded():
    result = _validate_output(
        {"bias": "neutral", "confidence": 4, "thesis": "test", "evidence_ids": ["C-trend"]},
        {"C-trend"},
    )
    assert result["confidence"] == 1.0
