from __future__ import annotations

import pytest

from evals.metrics import aggregate_runs, percentile, summarize_run


def test_percentile_uses_nearest_rank_for_p95() -> None:
    assert percentile([100, 200, 300, 400], 95) == 400


def test_summarize_run_counts_failed_tools_and_preserves_metrics() -> None:
    result = summarize_run(
        {
            "success": True,
            "duration_ms": 1234,
            "token_usage": {"total_tokens": 321},
            "tool_events": [
                {"type": "tool.finished", "failed": False},
                {"type": "tool.finished", "failed": True},
            ],
        }
    )
    assert result == {
        "success": True,
        "tool_errors": 1,
        "duration_ms": 1234,
        "total_tokens": 321,
    }


def test_aggregate_runs_reports_success_rate_tool_error_rate_and_p95() -> None:
    report = aggregate_runs(
        [
            {"success": True, "duration_ms": 100, "total_tokens": 10, "tool_errors": 0},
            {"success": False, "duration_ms": 200, "total_tokens": 20, "tool_errors": 2},
            {"success": True, "duration_ms": 300, "total_tokens": 30, "tool_errors": 1},
        ]
    )
    assert report["runs"] == 3
    assert report["success_rate"] == pytest.approx(2 / 3)
    assert report["tool_error_rate"] == pytest.approx(1.0)
    assert report["total_tokens"] == 60
    assert report["p95_latency_ms"] == 300
