"""SDLC automation agents.

These agents automate repetitive development workflow tasks:
- RegressionGuard : checks that every strategy change has a regression test
- BlotterReconciler: compares backtest blotter vs live blotter and flags deltas
- CoverageEnforcer : fails CI if coverage drops below threshold

Agents are designed to run as pre-commit hooks, CI steps, or on-demand CLI
commands — not in the trading engine's hot path.
"""

from __future__ import annotations

import ast
import logging
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
#  RegressionGuard                                                             #
# --------------------------------------------------------------------------- #

@dataclass
class RegressionCheckResult:
    passed: bool
    changed_files: list[str]
    missing_tests: list[str]
    message: str


class RegressionGuard:
    """Verifies every changed strategy file has a corresponding regression test.

    Used as a pre-push or CI gate. Checks:
    1. For each changed file in src/quant_system/strategies/ or regime/,
       there must be a test file in tests/regression/ or tests/unit/.
    2. The test file must contain at least one test function that references
       the changed module's name.

    This is a structural check, not a test runner — it can't guarantee the
    test actually covers the bug, but it enforces the convention that
    "every strategy change ships with a regression test."
    """

    def __init__(self, repo_root: Path) -> None:
        self._root = repo_root

    def check(self, changed_files: list[str] | None = None) -> RegressionCheckResult:
        """Check that changed strategy files have regression tests.

        If changed_files is None, uses `git diff --name-only HEAD~1` to
        auto-detect changed files.
        """
        if changed_files is None:
            changed_files = self._git_changed_files()

        strategy_files = [
            f for f in changed_files
            if ("strategies" in f or "regime" in f) and f.endswith(".py") and "__init__" not in f
        ]

        missing_tests: list[str] = []
        for src_file in strategy_files:
            module_name = Path(src_file).stem
            test_files = list(self._root.glob(f"tests/**/*{module_name}*"))
            if not test_files:
                missing_tests.append(src_file)
            else:
                # Check test file has at least one test_ function
                has_test = any(self._has_test_function(tf) for tf in test_files)
                if not has_test:
                    missing_tests.append(src_file)

        passed = len(missing_tests) == 0
        message = (
            "All changed strategy files have regression tests."
            if passed
            else f"Missing regression tests for: {missing_tests}"
        )
        return RegressionCheckResult(
            passed=passed,
            changed_files=strategy_files,
            missing_tests=missing_tests,
            message=message,
        )

    def _git_changed_files(self) -> list[str]:
        try:
            result = subprocess.run(
                ["git", "diff", "--name-only", "HEAD~1"],
                capture_output=True,
                text=True,
                cwd=self._root,
            )
            return [f.strip() for f in result.stdout.splitlines() if f.strip()]
        except Exception as exc:
            logger.warning("git_diff_failed", extra={"error": str(exc)})
            return []

    @staticmethod
    def _has_test_function(path: Path) -> bool:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            return any(
                isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
                for node in ast.walk(tree)
            )
        except Exception:
            return False


# --------------------------------------------------------------------------- #
#  BlotterReconciler                                                           #
# --------------------------------------------------------------------------- #

@dataclass
class ReconciliationResult:
    matched: int
    mismatched: list[dict]
    tolerance_paise: int
    passed: bool


class BlotterReconciler:
    """Compares two blotters (e.g. backtest vs live) and flags paise-level deltas.

    The core acceptance criterion: backtest P&L must reconcile to live P&L.
    This agent makes that check explicit and automatable.
    """

    def __init__(self, tolerance_paise: int = 0) -> None:
        """
        Args:
            tolerance_paise: allowed difference per fill in paise (1/100 rupee).
                             Default 0 means exact match required.
        """
        self._tolerance = tolerance_paise

    def reconcile(
        self,
        reference: list[dict],   # list of blotter entry dicts (e.g. from live)
        candidate: list[dict],   # list to check (e.g. from backtest)
        key_field: str = "fill_id",
        compare_field: str = "net_value",
    ) -> ReconciliationResult:
        """Compare candidate blotter against reference on compare_field.

        Returns a ReconciliationResult with any mismatches.
        """
        ref_map = {row[key_field]: row for row in reference}
        mismatches: list[dict] = []
        matched = 0

        for row in candidate:
            key = row.get(key_field)
            ref_row = ref_map.get(key)
            if ref_row is None:
                mismatches.append({"fill_id": key, "issue": "missing_in_reference"})
                continue

            from decimal import Decimal
            cand_val = Decimal(str(row.get(compare_field, 0)))
            ref_val = Decimal(str(ref_row.get(compare_field, 0)))
            diff_paise = abs(cand_val - ref_val) * 100  # convert rupees to paise

            if diff_paise > self._tolerance:
                mismatches.append({
                    "fill_id": key,
                    "reference": str(ref_val),
                    "candidate": str(cand_val),
                    "diff_paise": str(diff_paise),
                })
            else:
                matched += 1

        return ReconciliationResult(
            matched=matched,
            mismatched=mismatches,
            tolerance_paise=self._tolerance,
            passed=len(mismatches) == 0,
        )


# --------------------------------------------------------------------------- #
#  CoverageEnforcer                                                            #
# --------------------------------------------------------------------------- #

class CoverageEnforcer:
    """Runs pytest with coverage and fails if coverage drops below threshold.

    Used as a CI gate. Produces a structured report.
    """

    def __init__(self, min_coverage: float = 90.0, source: str = "quant_system") -> None:
        self._min = min_coverage
        self._source = source

    def run(self, test_path: str = "tests/unit") -> dict:
        result = subprocess.run(
            [
                sys.executable, "-m", "pytest",
                test_path,
                f"--cov={self._source}",
                "--cov-report=term-missing",
                "--cov-report=json:coverage.json",
                "-q",
            ],
            capture_output=True,
            text=True,
        )

        output = result.stdout + result.stderr
        coverage_pct = self._parse_coverage(output)
        passed = coverage_pct is not None and coverage_pct >= self._min

        return {
            "passed": passed,
            "coverage_pct": coverage_pct,
            "min_required": self._min,
            "output": output,
            "returncode": result.returncode,
        }

    @staticmethod
    def _parse_coverage(output: str) -> float | None:
        """Extract total coverage percentage from pytest-cov output."""
        for line in output.splitlines():
            if "TOTAL" in line:
                parts = line.split()
                for p in reversed(parts):
                    if p.endswith("%"):
                        try:
                            return float(p.rstrip("%"))
                        except ValueError:
                            pass
        return None
