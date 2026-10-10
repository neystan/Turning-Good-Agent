from evals.cases import COMPREHENSIVE_CASES, SECURITY_CASES


def test_security_suite_covers_required_failure_injection_categories() -> None:
    categories = {case["category"] for case in SECURITY_CASES}
    assert categories == {
        "path_traversal",
        "sensitive_read",
        "unauthorized_tool",
        "approval_denied",
        "timeout_cancel",
        "worker_failure_fallback",
    }


def test_comprehensive_suite_contains_twelve_long_tasks_with_both_modes() -> None:
    assert len(COMPREHENSIVE_CASES) == 12
    assert all(case["prompt"] and case["expected_markers"] for case in COMPREHENSIVE_CASES)
