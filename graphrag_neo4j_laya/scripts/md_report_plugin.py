"""
pytest plugin: writes a Markdown report of every test's outcome, with the
reason for each fail / error / xfail / xpass / skip.

Usage:
    python -m pytest -p md_report_plugin --md-report=test-reports/test_report.md
(`scripts/` must be on PYTHONPATH; run_tests_report.cmd does that.)
"""

from __future__ import annotations

import platform
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import pytest

ORDER = ["failed", "error", "xpassed", "xfailed", "skipped", "passed"]
LABEL = {
    "passed": "PASS",
    "failed": "FAIL",
    "error": "ERROR",
    "skipped": "SKIP",
    "xfailed": "XFAIL",
    "xpassed": "XPASS",
}


def pytest_addoption(parser):
    parser.addoption("--md-report", default="test-reports/test_report.md",
                     help="Markdown report path")


def pytest_configure(config):
    if not hasattr(config, "workerinput"):
        config.pluginmanager.register(_MdReport(config), "md_report")


def _outcome(report) -> str | None:
    """Final outcome of one test-phase report, or None if it adds nothing."""
    if report.when == "call":
        if hasattr(report, "wasxfail"):
            return "xfailed" if report.skipped else "xpassed"
        return report.outcome
    if report.failed:
        return "error"  # setup/teardown failure
    if report.skipped and report.when == "setup":
        return "xfailed" if hasattr(report, "wasxfail") else "skipped"
    return None


def _reason(report, outcome: str) -> str:
    if outcome in ("xfailed", "xpassed"):
        return report.wasxfail.removeprefix("reason: ") or "(no reason given)"
    if outcome == "skipped":
        lr = report.longrepr
        if isinstance(lr, tuple) and len(lr) == 3:
            return str(lr[2]).removeprefix("Skipped: ")
        return str(lr)
    if outcome in ("failed", "error"):
        crash = getattr(report.longrepr, "reprcrash", None)
        if crash is not None:
            return crash.message
        return str(report.longrepr).strip().splitlines()[-1]
    return ""


def _cell(text: str) -> str:
    text = " ".join(text.split())
    if len(text) > 800:
        text = text[:797] + "..."
    return text.replace("|", "\|")


class _MdReport:
    def __init__(self, config):
        self.config = config
        self.path = Path(config.getoption("--md-report"))
        self.results: dict[str, dict] = {}
        self.collect_errors: list[tuple[str, str]] = []
        self.start = time.time()

    def pytest_runtest_logreport(self, report):
        outcome = _outcome(report)
        entry = self.results.setdefault(
            report.nodeid, {"outcome": "passed", "reason": "", "detail": "", "duration": 0.0}
        )
        entry["duration"] += report.duration
        if outcome is None or outcome == "passed":
            return
        # keep the most severe outcome (e.g. a teardown error after a pass)
        if ORDER.index(outcome) < ORDER.index(entry["outcome"]) or entry["outcome"] == "passed":
            entry["outcome"] = outcome
            entry["reason"] = _reason(report, outcome)
            if outcome in ("failed", "error"):
                entry["detail"] = f"[{report.when}]\n{report.longreprtext}"

    def pytest_collectreport(self, report):
        if report.failed:
            self.collect_errors.append((report.nodeid or "<session>", report.longreprtext))

    def pytest_sessionfinish(self, session, exitstatus):
        counts = Counter(r["outcome"] for r in self.results.values())
        total = len(self.results)
        elapsed = time.time() - self.start
        lines = [
            "# Test Report",
            "",
            f"- Date: {datetime.now():%Y-%m-%d %H:%M:%S}",
            f"- Python: {platform.python_version()} ({sys.executable})",
            f"- pytest: {pytest.__version__}",
            f"- Args: `{' '.join(self.config.invocation_params.args) or '(none)'}`",
            f"- Duration: {elapsed:.2f}s",
            f"- Exit status: {int(exitstatus)}",
            "",
            "## Summary",
            "",
            "| Status | Count |",
            "|---|---:|",
        ]
        for key in ORDER:
            lines.append(f"| {LABEL[key]} | {counts.get(key, 0)} |")
        lines.append(f"| **Total** | **{total}** |")
        if self.collect_errors:
            lines.append(f"| COLLECTION ERROR | {len(self.collect_errors)} |")

        if self.collect_errors:
            lines += ["", "## Collection errors", ""]
            for nodeid, text in self.collect_errors:
                lines += [f"### `{nodeid}`", "", "```text", text.rstrip(), "```", ""]

        lines += ["", "## Non-passing tests", ""]
        bad = [(n, r) for n, r in self.results.items() if r["outcome"] != "passed"]
        bad.sort(key=lambda nr: (ORDER.index(nr[1]["outcome"]), nr[0]))
        if bad:
            lines += ["| Status | Test | Reason |", "|---|---|---|"]
            for nodeid, r in bad:
                lines.append(f"| {LABEL[r['outcome']]} | `{nodeid}` | {_cell(r['reason'])} |")
        else:
            lines.append("None.")

        failures = [(n, r) for n, r in bad if r["detail"]]
        if failures:
            lines += ["", "## Failure details", ""]
            for nodeid, r in failures:
                lines += [f"### {LABEL[r['outcome']]}: `{nodeid}`", "",
                          "```text", r["detail"].rstrip(), "```", ""]

        lines += ["", "## All tests", "", "| Status | Test | Duration (s) |", "|---|---|---:|"]
        for nodeid, r in self.results.items():
            lines.append(f"| {LABEL[r['outcome']]} | `{nodeid}` | {r['duration']:.3f} |")

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def pytest_terminal_summary(self, terminalreporter):
        terminalreporter.write_sep("-", f"Markdown report: {self.path.resolve()}")
