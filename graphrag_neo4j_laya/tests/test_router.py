"""
tests/test_router.py — IntentRouter with the optional aggregate intent.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from config.settings import settings
from graphrag.models.base_decision import DecisionResult
from graphrag.retrieval.router import IntentRouter, QueryIntent


def _router(selected: str, probs: dict[str, float]) -> tuple[IntentRouter, MagicMock]:
    model = MagicMock()
    model.choice_detailed.return_value = DecisionResult(
        score=probs[selected], confidence=probs[selected], raw_probs=probs,
        latency_ms=0.0, backend="mock", primitive="choice", selected=selected,
    )
    with patch("graphrag.retrieval.router.get_decision_model", return_value=model):
        return IntentRouter(), model


class TestRouterOptions:
    def test_flag_off_offers_the_original_three_routes(self, monkeypatch):
        monkeypatch.setattr(settings, "aggregate_route_enabled", False)
        router, model = _router("local", {"local": 0.7, "multi_hop": 0.2, "global": 0.1})
        assert router.route("Where was Newton born?") == QueryIntent.LOCAL
        options = model.choice_detailed.call_args.args[2]
        assert list(options) == ["local", "multi_hop", "global"]

    def test_flag_on_offers_aggregate(self, monkeypatch):
        monkeypatch.setattr(settings, "aggregate_route_enabled", True)
        router, model = _router("aggregate", {"local": 0.1, "multi_hop": 0.2, "global": 0.1, "aggregate": 0.6})
        assert router.route("How many theories are there?") == QueryIntent.AGGREGATE
        assert "aggregate" in model.choice_detailed.call_args.args[2]


class TestRouteDetailed:
    def test_confidence_and_fallback(self, monkeypatch):
        monkeypatch.setattr(settings, "aggregate_route_enabled", True)
        router, _ = _router("aggregate", {"local": 0.1, "multi_hop": 0.25, "global": 0.05, "aggregate": 0.6})
        decision = router.route_detailed("How many theories are there?")
        assert decision.intent == QueryIntent.AGGREGATE
        assert decision.confidence == pytest.approx(0.6)
        assert decision.fallback == QueryIntent.MULTI_HOP

    def test_fallback_is_never_aggregate(self, monkeypatch):
        monkeypatch.setattr(settings, "aggregate_route_enabled", True)
        router, _ = _router("aggregate", {"local": 0.0, "multi_hop": 0.0, "global": 0.0, "aggregate": 1.0})
        assert router.route_detailed("q").fallback != QueryIntent.AGGREGATE
