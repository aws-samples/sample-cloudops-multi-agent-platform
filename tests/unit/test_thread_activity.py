"""Unit tests for agents.shared.thread_activity — per-thread busy state."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

# Reset the module-level client between tests so each test gets a fresh mock.
@pytest.fixture(autouse=True)
def _reset_client(monkeypatch):
    from agents.shared import thread_activity as ta
    ta._ddb_client = None
    monkeypatch.setenv("REPORT_TABLE_NAME", "test-reports-table")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    yield
    ta._ddb_client = None


def _make_mock_ddb():
    mock = MagicMock()
    # Attach an exceptions namespace the module uses for ConditionalCheckFailed.
    mock.exceptions.ConditionalCheckFailedException = Exception
    return mock


class TestMarkThreadRunning:
    def test_writes_running_row(self):
        from agents.shared import thread_activity as ta
        mock = _make_mock_ddb()
        with patch("agents.shared.thread_activity.boto3.client", return_value=mock):
            ta.mark_thread_running("thread-1", "user-1", "Starting", run_id="run-42")

        mock.put_item.assert_called_once()
        call = mock.put_item.call_args.kwargs
        assert call["TableName"] == "test-reports-table"
        item = call["Item"]
        assert item["userId"]["S"] == "thread-activity:user-1"
        assert item["templateId"]["S"] == "thread-1"
        assert item["status"]["S"] == "running"
        assert item["currentStep"]["S"] == "Starting"
        assert item["runId"]["S"] == "run-42"
        assert "startedAt" in item and "updatedAt" in item

    def test_omits_report_id_when_absent(self):
        from agents.shared import thread_activity as ta
        mock = _make_mock_ddb()
        with patch("agents.shared.thread_activity.boto3.client", return_value=mock):
            ta.mark_thread_running("thread-1", "user-1", "Step", run_id="")

        item = mock.put_item.call_args.kwargs["Item"]
        assert "reportId" not in item
        assert "runId" not in item  # empty run_id is also skipped

    def test_includes_report_id_when_given(self):
        from agents.shared import thread_activity as ta
        mock = _make_mock_ddb()
        with patch("agents.shared.thread_activity.boto3.client", return_value=mock):
            ta.mark_thread_running("t", "u", "Gen", report_id="r_abc")
        item = mock.put_item.call_args.kwargs["Item"]
        assert item["reportId"]["S"] == "r_abc"

    def test_no_table_env_var_is_a_noop(self, monkeypatch):
        from agents.shared import thread_activity as ta
        monkeypatch.setenv("REPORT_TABLE_NAME", "")
        mock = _make_mock_ddb()
        with patch("agents.shared.thread_activity.boto3.client", return_value=mock):
            ta.mark_thread_running("t", "u", "step")
        mock.put_item.assert_not_called()

    def test_missing_ids_are_noop(self):
        from agents.shared import thread_activity as ta
        mock = _make_mock_ddb()
        with patch("agents.shared.thread_activity.boto3.client", return_value=mock):
            ta.mark_thread_running("", "u", "step")
            ta.mark_thread_running("t", "", "step")
        mock.put_item.assert_not_called()


class TestUpdateThreadStep:
    def test_updates_step_with_conditional_running(self):
        from agents.shared import thread_activity as ta
        mock = _make_mock_ddb()
        with patch("agents.shared.thread_activity.boto3.client", return_value=mock):
            ta.update_thread_step("t", "u", "Calling tool X")

        mock.update_item.assert_called_once()
        kwargs = mock.update_item.call_args.kwargs
        assert "currentStep = :s" in kwargs["UpdateExpression"]
        assert kwargs["ConditionExpression"] == "#st = :running"
        assert kwargs["ExpressionAttributeValues"][":s"]["S"] == "Calling tool X"

    def test_conditional_check_failed_is_swallowed(self):
        """Updating an idle or missing row must not raise."""
        from agents.shared import thread_activity as ta
        mock = _make_mock_ddb()

        class _CondFail(Exception):
            pass

        mock.exceptions.ConditionalCheckFailedException = _CondFail
        mock.update_item.side_effect = _CondFail("not running")
        with patch("agents.shared.thread_activity.boto3.client", return_value=mock):
            ta.update_thread_step("t", "u", "step")  # should not raise

    def test_empty_step_is_noop(self):
        from agents.shared import thread_activity as ta
        mock = _make_mock_ddb()
        with patch("agents.shared.thread_activity.boto3.client", return_value=mock):
            ta.update_thread_step("t", "u", "")
        mock.update_item.assert_not_called()


class TestMarkThreadIdle:
    def test_sets_status_idle_and_clears_step(self):
        from agents.shared import thread_activity as ta
        mock = _make_mock_ddb()
        with patch("agents.shared.thread_activity.boto3.client", return_value=mock):
            ta.mark_thread_idle("t", "u")
        kwargs = mock.update_item.call_args.kwargs
        vals = kwargs["ExpressionAttributeValues"]
        assert vals[":idle"]["S"] == "idle"
        assert vals[":empty"]["S"] == ""


class TestMarkThreadError:
    def test_truncates_long_error_messages(self):
        from agents.shared import thread_activity as ta
        mock = _make_mock_ddb()
        long_err = "x" * 1200
        with patch("agents.shared.thread_activity.boto3.client", return_value=mock):
            ta.mark_thread_error("t", "u", long_err)
        err_written = mock.update_item.call_args.kwargs[
            "ExpressionAttributeValues"
        ][":e"]["S"]
        assert len(err_written) == 500  # capped


class TestGetThreadActivity:
    def test_returns_idle_when_row_missing(self):
        from agents.shared import thread_activity as ta
        mock = _make_mock_ddb()
        mock.get_item.return_value = {}  # no Item key
        with patch("agents.shared.thread_activity.boto3.client", return_value=mock):
            result = ta.get_thread_activity("t", "u")
        assert result == {"status": "idle"}

    def test_returns_full_row_when_running(self):
        from agents.shared import thread_activity as ta
        now = datetime.now(timezone.utc).isoformat()
        mock = _make_mock_ddb()
        mock.get_item.return_value = {
            "Item": {
                "status": {"S": "running"},
                "currentStep": {"S": "Working"},
                "startedAt": {"S": now},
                "updatedAt": {"S": now},
                "runId": {"S": "r1"},
                "reportId": {"S": "rep-1"},
                "errorMsg": {"S": ""},
            }
        }
        with patch("agents.shared.thread_activity.boto3.client", return_value=mock):
            result = ta.get_thread_activity("t", "u")
        assert result["status"] == "running"
        assert result["current_step"] == "Working"
        assert result["report_id"] == "rep-1"

    def test_stale_running_row_is_reported_idle(self):
        from agents.shared import thread_activity as ta
        stale_ts = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
        mock = _make_mock_ddb()
        mock.get_item.return_value = {
            "Item": {
                "status": {"S": "running"},
                "updatedAt": {"S": stale_ts},
                "currentStep": {"S": "stuck"},
            }
        }
        with patch("agents.shared.thread_activity.boto3.client", return_value=mock):
            result = ta.get_thread_activity("t", "u")
        assert result == {"status": "idle"}

    def test_ddb_error_returns_idle_safely(self):
        from agents.shared import thread_activity as ta
        mock = _make_mock_ddb()
        mock.get_item.side_effect = RuntimeError("ddb down")
        with patch("agents.shared.thread_activity.boto3.client", return_value=mock):
            result = ta.get_thread_activity("t", "u")
        assert result == {"status": "idle"}


class TestIsStale:
    def test_recent_ts_is_not_stale(self):
        from agents.shared import thread_activity as ta
        recent = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
        assert ta._is_stale(recent) is False

    def test_old_ts_is_stale(self):
        from agents.shared import thread_activity as ta
        old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        assert ta._is_stale(old) is True

    def test_empty_ts_is_stale(self):
        from agents.shared import thread_activity as ta
        assert ta._is_stale("") is True

    def test_malformed_ts_is_stale(self):
        from agents.shared import thread_activity as ta
        assert ta._is_stale("not a date") is True
