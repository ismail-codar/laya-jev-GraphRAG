"""
tests/test_laya.py — Unit tests for the Laya model wrapper.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _make_laya(answer: dict) -> "LayaModel":  # noqa: F821
    """Build a LayaModel whose `laya` agent returns *answer* for question 'q'."""
    from graphrag.models.laya import LayaModel

    laya = LayaModel.__new__(LayaModel)
    laya._initialised = True
    laya.agent = MagicMock()
    laya.agent.predict.return_value = {"answers": {"q": answer}}
    return laya


def _score_answer(level: float, probs: list[float]) -> dict:
    return {
        "type": "score",
        "score": level,
        "legend": {"0": "irrelevant", "1": "tangential", "2": "critical"},
        "probabilities": {str(i): p for i, p in enumerate(probs)},
        "answer_confidence": max(probs),
    }


class TestLayaModelScore:
    """Test LayaModel.score() with a mocked `laya` agent."""

    def test_critical_label_returns_high_score(self):
        laya = _make_laya(_score_answer(1.98, [0.0, 0.02, 0.98]))
        score = laya.score("context", "instruction")
        assert score > 0.9, f"Expected score close to 1.0, got {score}"

    def test_irrelevant_label_returns_low_score(self):
        laya = _make_laya(_score_answer(0.02, [0.98, 0.02, 0.0]))
        score = laya.score("context", "instruction")
        assert score < 0.1, f"Expected score close to 0.0, got {score}"

    def test_score_is_float_in_unit_range(self):
        laya = _make_laya(_score_answer(1.0, [0.25, 0.5, 0.25]))
        score = laya.score("context", "instruction")
        assert isinstance(score, float)
        assert score == pytest.approx(0.5)

    def test_score_sends_ordinal_criteria(self):
        laya = _make_laya(_score_answer(1.0, [0.25, 0.5, 0.25]))
        laya.score("ctx", "How relevant?")
        _, questions = laya.agent.predict.call_args.args
        assert questions["q"]["type"] == "score"
        assert questions["q"]["criteria"] == ["irrelevant", "tangential", "critical"]


class TestLayaModelNoulChoice:
    def test_noul_returns_p_true(self):
        laya = _make_laya({"type": "noul", "noul": 0.81, "answer_confidence": 0.81})
        assert laya.noul("ctx", "Is it?") == pytest.approx(0.81)

    def test_choice_detailed_returns_selected_key(self):
        laya = _make_laya({
            "type": "choice",
            "choice": "multi_hop",
            "probabilities": {"local": 0.2, "multi_hop": 0.7, "global": 0.1},
            "answer_confidence": 0.7,
        })
        result = laya.choice_detailed("ctx", "Route?", {"local": "a", "multi_hop": "b", "global": "c"})
        assert result.selected == "multi_hop"
        assert result.score == pytest.approx(0.7)
        assert result.backend == "laya"


class TestLayaChunker:
    """Test NoulBoundaryChunker with a mocked decision model."""

    def test_single_sentence_returns_one_chunk(self):
        with patch("graphrag.ingestion.chunker.get_decision_model") as mock_get_model:
            mock_model = MagicMock()
            mock_model.noul.return_value = 0.1  # never a boundary
            mock_get_model.return_value = mock_model

            from graphrag.ingestion.chunker import NoulBoundaryChunker
            chunker = NoulBoundaryChunker(threshold=0.85)
            result = chunker.chunk("Only one sentence here.")
            assert len(result) == 1

    def test_high_boundary_score_creates_chunks(self):
        with patch("graphrag.ingestion.chunker.get_decision_model") as mock_get_model:
            mock_model = MagicMock()
            mock_model.noul.return_value = 0.95  # always a boundary
            mock_get_model.return_value = mock_model

            from graphrag.ingestion.chunker import NoulBoundaryChunker
            chunker = NoulBoundaryChunker(threshold=0.85, min_chunk_sentences=1)
            text = "First sentence. Second sentence. Third sentence."
            result = chunker.chunk(text)
            assert len(result) > 1
