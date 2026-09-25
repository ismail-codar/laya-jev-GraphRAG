"""
graphrag/models/laya.py

Thin, singleton-aware wrapper around the Laya System One decision model,
loaded through the official `laya` package (`pip install laya`).

Default checkpoint: convaiinnovations/laya  (subfolder: multilingual)
        Apache 2.0 · mmBERT-base backbone · 322M params · 100+ languages
        Runs on CUDA when available, otherwise CPU.

Other checkpoints (set LAYA_MODEL_SUBFOLDER in .env):
        ""                → English, ModernBERT-large, 421M
        "typed-decisions" → English specialist (4 synthetic workflows)

All three decision primitives are implemented:

  score(context, instruction)               → float [0,1]  ordinal relevance
  noul(context, instruction)                → float [0,1]  P(yes)
  choice(context, instruction, options)     → str   selected option key

S_Laya formula (idea.md §2.1):
  S_Laya = P(Critical)·1.0 + P(Tangential)·0.5 + P(Irrelevant)·0.0
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import defaultdict
from functools import lru_cache
from typing import Any

from config.settings import settings
from .base_decision import BaseDecisionModel, DecisionResult

logger = logging.getLogger(__name__)

# transformers probes for TensorFlow at import; its abseil runtime can deadlock
# Laya model construction (see the Laya model card).
os.environ.setdefault("USE_TF", "0")

# ── Score levels (ordinal, low → high) ────────────────────────────────────────
# Laya returns the expected level index in [0, n-1]; normalising by (n-1) gives
# exactly S_Laya = P(critical)·1.0 + P(tangential)·0.5 + P(irrelevant)·0.0
_SCORE_CRITERIA = ["irrelevant", "tangential", "critical"]


def _score_question(instruction: str) -> dict[str, Any]:
    return {"type": "score", "instructions": instruction, "criteria": _SCORE_CRITERIA}


def _noul_question(instruction: str) -> dict[str, Any]:
    return {"type": "noul", "instructions": instruction}


def _choice_question(instruction: str, options: dict[str, str]) -> dict[str, Any]:
    return {"type": "choice", "instructions": instruction, "criteria": options}


def _normalise_score(answer: dict[str, Any]) -> float:
    levels = max(len(answer.get("legend", _SCORE_CRITERIA)) - 1, 1)
    return min(max(float(answer["score"]) / levels, 0.0), 1.0)


class LayaModel(BaseDecisionModel):
    """
    Singleton wrapper for the Laya decision model.
    Thread-safe: the internal lock prevents duplicate model loads.
    """

    _instance: "LayaModel | None" = None
    _lock: threading.Lock = threading.Lock()

    def __new__(cls) -> "LayaModel":
        with cls._lock:
            if cls._instance is None:
                obj = object.__new__(cls)
                obj._initialised = False
                cls._instance = obj
        return cls._instance

    def __init__(self) -> None:
        if self._initialised:
            return
        self._initialised = True
        self._load()

    def _load(self) -> None:
        import laya  # noqa: PLC0415 — heavy import, deferred until first use

        model_id  = settings.laya_model_id
        subfolder = settings.laya_model_subfolder or None
        device    = settings.laya_device or None
        logger.info("Loading Laya model: %s (subfolder=%s)", model_id, subfolder)
        t0 = time.perf_counter()
        self.agent = laya.load(
            model_id,
            subfolder=subfolder,
            device=device,
            token=settings.huggingface_token or None,
        )
        logger.info("Laya ready in %.1fs", time.perf_counter() - t0)

    # ── Internal predict ──────────────────────────────────────────────────────

    def _predict(self, context: str, questions: dict[str, dict]) -> tuple[dict[str, Any], float]:
        """Run one parallel forward pass; return (answers, latency_ms)."""
        t0 = time.perf_counter()
        result = self.agent.predict(context, questions)
        latency_ms = (time.perf_counter() - t0) * 1000
        return result["answers"], latency_ms

    def _to_result(self, answer: dict[str, Any], latency_ms: float) -> DecisionResult:
        kind = answer["type"]
        if kind == "score":
            value = _normalise_score(answer)
            raw   = {_SCORE_CRITERIA[int(k)]: v for k, v in answer["probabilities"].items()}
            selected = None
        elif kind == "noul":
            value = float(answer["noul"])
            raw   = {"no": round(1.0 - value, 4), "yes": round(value, 4)}
            selected = None
        else:
            selected = answer["choice"]
            raw   = dict(answer["probabilities"])
            value = float(raw[selected])
        return DecisionResult(
            score=value,
            confidence=float(answer.get("answer_confidence", answer.get("confidence", 0.0))),
            raw_probs=raw,
            latency_ms=round(latency_ms, 2),
            backend="laya",
            primitive=kind,
            selected=selected,
        )

    # ── Score Primitive ───────────────────────────────────────────────────────

    def score(self, context: str, instruction: str) -> float:
        answers, _ = self._predict(context, {"q": _score_question(instruction)})
        return _normalise_score(answers["q"])

    def score_detailed(self, context: str, instruction: str) -> DecisionResult:
        answers, latency_ms = self._predict(context, {"q": _score_question(instruction)})
        return self._to_result(answers["q"], latency_ms)

    # ── Noul Primitive ────────────────────────────────────────────────────────

    def noul(self, context: str, instruction: str) -> float:
        """P(yes) for a binary question about *context*."""
        answers, _ = self._predict(context, {"q": _noul_question(instruction)})
        return float(answers["q"]["noul"])

    def noul_detailed(self, context: str, instruction: str) -> DecisionResult:
        answers, latency_ms = self._predict(context, {"q": _noul_question(instruction)})
        return self._to_result(answers["q"], latency_ms)

    # ── Choice Primitive ──────────────────────────────────────────────────────

    def choice(self, context: str, instruction: str, options: dict[str, str]) -> str:
        """Categorical selection — all options are scored in a single forward pass."""
        if not options:
            raise ValueError("choice() requires at least one option.")
        answers, _ = self._predict(context, {"q": _choice_question(instruction, options)})
        return answers["q"]["choice"]

    def choice_detailed(
        self, context: str, instruction: str, options: dict[str, str]
    ) -> DecisionResult:
        if not options:
            raise ValueError("choice_detailed() requires at least one option.")
        answers, latency_ms = self._predict(context, {"q": _choice_question(instruction, options)})
        return self._to_result(answers["q"], latency_ms)

    # ── Multi-question (one forward pass) ─────────────────────────────────────

    def ask_batch(self, state: str, questions: dict[str, dict]) -> dict[str, DecisionResult]:
        laya_questions: dict[str, dict] = {}
        for name, spec in questions.items():
            q_type = spec["type"]
            instruction = spec.get("instruction", "")
            if q_type == "score":
                laya_questions[name] = _score_question(instruction)
            elif q_type == "noul":
                laya_questions[name] = _noul_question(instruction)
            elif q_type == "choice":
                laya_questions[name] = _choice_question(instruction, spec.get("options", {}))
            else:
                raise ValueError(f"Unknown question type: {q_type!r}")
        answers, latency_ms = self._predict(state, laya_questions)
        return {name: self._to_result(answers[name], latency_ms) for name in questions}

    # ── Batch Score ───────────────────────────────────────────────────────────

    def batch_score(
        self,
        pairs: list[tuple[str, str]],
        batch_size: int = 16,
    ) -> list[float]:
        """Score many pairs; pairs sharing an instruction share forward passes."""
        results: list[float] = [0.0] * len(pairs)
        by_instruction: dict[str, list[int]] = defaultdict(list)
        for i, (_, instruction) in enumerate(pairs):
            by_instruction[instruction].append(i)
        for instruction, idxs in by_instruction.items():
            outputs = self.agent.predict_batch(
                [pairs[i][0] for i in idxs],
                {"q": _score_question(instruction)},
                batch_size=batch_size,
            )
            for i, out in zip(idxs, outputs):
                results[i] = _normalise_score(out["answers"]["q"])
        return results


@lru_cache(maxsize=1)
def get_laya() -> LayaModel:
    """Return the global Laya singleton (lazy-loaded on first call)."""
    return LayaModel()
