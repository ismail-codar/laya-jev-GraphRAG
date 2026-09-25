"""
tests/scripted_model.py

Decision model double for planner tests: Choice answers come from a queue
(falling back to the first non-stop option), Noul answers from a queue
(falling back to 0.1), batched Noul answers from a dict.
"""

from __future__ import annotations

from graphrag.models.base_decision import DecisionResult


def _result(primitive: str, selected: str | None, probs: dict[str, float]) -> DecisionResult:
    score = probs[selected] if selected else probs["yes"]
    return DecisionResult(score=score, confidence=score, raw_probs=probs, latency_ms=0.0,
                          backend="scripted", primitive=primitive, selected=selected)


class ScriptedModel:
    """Answers Choice calls from a queue and Noul calls from a queue or a default."""

    def __init__(self, choices: list[str] | None = None, nouls: list[float] | None = None,
                 batch: dict[str, float] | None = None, prob: float = 0.9,
                 probs: list[float] | None = None) -> None:
        self.choices = list(choices or [])
        self.probs = list(probs or [])
        self.nouls = list(nouls or [])
        self.batch = batch or {}
        self.prob = prob
        self.choice_calls: list[dict[str, str]] = []
        self.batch_calls = 0
        self.noul_calls = 0

    def choice_detailed(self, context, instruction, options):
        self.choice_calls.append(options)
        selected = self.choices.pop(0) if self.choices else next(k for k in options if k != "stop")
        assert selected in options, f"{selected!r} not offered; options were {list(options)}"
        prob = self.probs.pop(0) if self.probs else self.prob
        rest = (1.0 - prob) / max(len(options) - 1, 1)
        return _result("choice", selected, {k: (prob if k == selected else rest) for k in options})

    def noul_detailed(self, context, instruction):
        self.noul_calls += 1
        p = self.nouls.pop(0) if self.nouls else 0.1
        return _result("noul", None, {"yes": p, "no": 1 - p})

    def ask_batch(self, state, questions):
        self.batch_calls += 1
        return {name: _result("noul", None, {"yes": self.batch.get(name, 0.1), "no": 0.9})
                for name in questions}
