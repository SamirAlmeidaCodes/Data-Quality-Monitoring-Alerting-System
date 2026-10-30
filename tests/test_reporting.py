"""Overall status ranking."""

from dqmonitor.reporting import attention_message, overall_status, summarize_counts


def test_fail_outranks_error_and_pending():
    assert overall_status(["pass", "error", "fail"]) == "fail"
    assert overall_status(["pass", "error"]) == "error"
    assert overall_status(["pass", "pending"]) == "pending"
    assert overall_status(["pass", "pass"]) == "pass"
    assert overall_status([]) == "unknown"


def test_summaries():
    assert summarize_counts(["pass", "pass"]) == "All 2 checks passed."
    assert summarize_counts(["fail", "pass", "error"]) == "3 checks: 1 failed, 1 errored, 1 passed."
    assert attention_message([{"status": "pass"}]) == "Every configured check passed."
    assert attention_message([{"status": "fail"}]) is None
