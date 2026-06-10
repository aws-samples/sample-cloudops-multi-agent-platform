"""Unit tests for agents.shared.report_tool — get_report Strands tool.

Boundary properties under test:
- Actor scoping: a tool bound to actor A cannot read actor B's reports
  even if A passes B's report_id verbatim.
- Fabricated/mistyped report_ids resolve to a structured "not found"
  error, never raise, never silently return empty.
- Partial-row case (status=in_progress, sections still pending) is
  surfaced honestly so the agent can tell the user the report is still
  generating instead of answering from incomplete data.
- Missing actor_id or REPORT_TABLE_NAME yield clear errors instead of
  a permissive default.
"""

from __future__ import annotations

import boto3
import pytest

from agents.shared.report_tool import make_get_report_tool

REPORT_TABLE = "test-reports"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def report_table(mock_aws_env):
    ddb = boto3.client("dynamodb", region_name="us-east-1")
    ddb.create_table(
        TableName=REPORT_TABLE,
        KeySchema=[
            {"AttributeName": "userId", "KeyType": "HASH"},
            {"AttributeName": "templateId", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "userId", "AttributeType": "S"},
            {"AttributeName": "templateId", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    return ddb


def _put_report(
    ddb,
    actor_id: str,
    report_id: str,
    title: str = "Test Report",
    status: str = "complete",
    sections: list | None = None,
    version: int = 1,
    parent_report_id: str = "",
):
    """Write a synthetic report row to DDB matching the production schema."""
    if sections is None:
        sections = [
            {
                "id": "s1",
                "title": "Section One",
                "status": "complete",
                "content": "## Heading\n\nBody of section one.",
                "error": "",
                "generated_at": "2026-05-21T08:00:00Z",
            }
        ]

    sections_l = []
    for s in sections:
        sections_l.append(
            {
                "M": {
                    "id": {"S": s.get("id", "")},
                    "title": {"S": s.get("title", "")},
                    "status": {"S": s.get("status", "complete")},
                    "content": {"S": s.get("content", "")},
                    "error": {"S": s.get("error", "")},
                    "generated_at": {"S": s.get("generated_at", "")},
                }
            }
        )

    item = {
        "userId": {"S": f"report:{actor_id}"},
        "templateId": {"S": report_id},
        "title": {"S": title},
        "status": {"S": status},
        "month": {"S": "May"},
        "year": {"S": "2026"},
        "createdAt": {"S": "2026-05-21T08:00:00Z"},
        "updatedAt": {"S": "2026-05-21T08:01:00Z"},
        "sections": {"L": sections_l},
        "currentSection": {"N": str(len(sections))},
        "totalSections": {"N": str(len(sections))},
        "version": {"N": str(version)},
    }
    if parent_report_id:
        item["parentReportId"] = {"S": parent_report_id}
    ddb.put_item(TableName=REPORT_TABLE, Item=item)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_reads_full_section_content(self, report_table):
        _put_report(
            report_table,
            actor_id="alice",
            report_id="report_abc",
            title="FinOps May Report",
            sections=[
                {
                    "id": "cost_overview",
                    "title": "Cost Overview",
                    "status": "complete",
                    "content": "Total spend: $12,345.67",
                    "error": "",
                    "generated_at": "2026-05-21T08:00:00Z",
                },
                {
                    "id": "savings",
                    "title": "Savings",
                    "status": "complete",
                    "content": "Top 3 recommendations...",
                    "error": "",
                    "generated_at": "2026-05-21T08:01:00Z",
                },
            ],
        )

        get_report = make_get_report_tool(
            actor_id="alice", region="us-east-1", table_name=REPORT_TABLE
        )
        result = get_report(report_id="report_abc")

        assert result["report_id"] == "report_abc"
        assert result["title"] == "FinOps May Report"
        assert result["status"] == "complete"
        assert len(result["sections"]) == 2
        assert result["sections"][0]["content"] == "Total spend: $12,345.67"
        assert result["sections"][1]["title"] == "Savings"
        assert "error" not in result

    def test_reads_edit_lineage(self, report_table):
        _put_report(
            report_table,
            actor_id="alice",
            report_id="report_v2",
            version=2,
            parent_report_id="report_v1",
        )
        get_report = make_get_report_tool(
            actor_id="alice", region="us-east-1", table_name=REPORT_TABLE
        )
        result = get_report(report_id="report_v2")
        assert result["version"] == 2
        assert result["parent_report_id"] == "report_v1"


# ---------------------------------------------------------------------------
# Actor scoping — the security boundary
# ---------------------------------------------------------------------------


class TestActorScoping:
    def test_cannot_read_other_actors_report(self, report_table):
        # Bob writes a report
        _put_report(
            report_table,
            actor_id="bob",
            report_id="report_bob_secret",
            title="Bob's Private Report",
        )
        # Alice's tool tries to read it by id
        get_report = make_get_report_tool(
            actor_id="alice", region="us-east-1", table_name=REPORT_TABLE
        )
        result = get_report(report_id="report_bob_secret")
        assert "error" in result
        assert "not found" in result["error"].lower()
        # Critically: must not leak the title/status/etc.
        assert "Bob's Private Report" not in str(result)

    def test_same_id_under_different_actors_is_isolated(self, report_table):
        # Both actors happen to have the same report_id (UUIDs collide is
        # vanishingly unlikely IRL, but the schema doesn't prevent it).
        _put_report(
            report_table,
            actor_id="alice",
            report_id="report_xyz",
            title="Alice's Report",
        )
        _put_report(
            report_table,
            actor_id="bob",
            report_id="report_xyz",
            title="Bob's Report",
        )
        alice_tool = make_get_report_tool(
            actor_id="alice", region="us-east-1", table_name=REPORT_TABLE
        )
        bob_tool = make_get_report_tool(
            actor_id="bob", region="us-east-1", table_name=REPORT_TABLE
        )
        assert alice_tool(report_id="report_xyz")["title"] == "Alice's Report"
        assert bob_tool(report_id="report_xyz")["title"] == "Bob's Report"


# ---------------------------------------------------------------------------
# Negative paths
# ---------------------------------------------------------------------------


class TestNegativePaths:
    def test_fabricated_report_id_returns_not_found(self, report_table):
        get_report = make_get_report_tool(
            actor_id="alice", region="us-east-1", table_name=REPORT_TABLE
        )
        result = get_report(report_id="report_doesnotexist")
        assert "error" in result
        assert "not found" in result["error"].lower()

    def test_empty_report_id_errors(self, report_table):
        get_report = make_get_report_tool(
            actor_id="alice", region="us-east-1", table_name=REPORT_TABLE
        )
        result = get_report(report_id="")
        assert "error" in result
        assert "required" in result["error"].lower()

    def test_missing_actor_id_refuses(self, report_table):
        get_report = make_get_report_tool(
            actor_id="", region="us-east-1", table_name=REPORT_TABLE
        )
        result = get_report(report_id="report_abc")
        assert "error" in result
        assert "actor" in result["error"].lower()

    def test_missing_table_name_refuses(self, report_table):
        # Even with a valid actor_id, no table name means we can't operate.
        get_report = make_get_report_tool(
            actor_id="alice", region="us-east-1", table_name=""
        )
        result = get_report(report_id="report_abc")
        assert "error" in result
        assert "not configured" in result["error"].lower()


# ---------------------------------------------------------------------------
# Partial-row / in-progress case
# ---------------------------------------------------------------------------


class TestPartialRow:
    def test_in_progress_report_status_surfaced(self, report_table):
        # Half-done report: 1 of 2 sections complete, top-level status
        # still in_progress. The composer is supposed to be locked while
        # this is true (the frontend disables input), so the agent
        # shouldn't normally be asked about it — but if it IS asked, it
        # needs the status field to refuse honestly.
        _put_report(
            report_table,
            actor_id="alice",
            report_id="report_running",
            status="in_progress",
            sections=[
                {
                    "id": "s1",
                    "title": "Section One",
                    "status": "complete",
                    "content": "Done",
                    "error": "",
                    "generated_at": "2026-05-21T08:00:00Z",
                },
                {
                    "id": "s2",
                    "title": "Section Two",
                    "status": "pending",
                    "content": "",
                    "error": "",
                    "generated_at": "",
                },
            ],
        )
        get_report = make_get_report_tool(
            actor_id="alice", region="us-east-1", table_name=REPORT_TABLE
        )
        result = get_report(report_id="report_running")
        assert result["status"] == "in_progress"
        # Both sections come back so the model can see exactly what's done
        assert len(result["sections"]) == 2
        assert result["sections"][0]["status"] == "complete"
        assert result["sections"][1]["status"] == "pending"
        assert result["sections"][1]["content"] == ""


# ---------------------------------------------------------------------------
# Tool metadata sanity (the no-fabrication preamble points at the tool
# name verbatim, so renaming would silently break the prompt rule).
# ---------------------------------------------------------------------------


class TestToolMetadata:
    def test_tool_is_named_get_report(self, report_table):
        get_report = make_get_report_tool(
            actor_id="alice", region="us-east-1", table_name=REPORT_TABLE
        )
        # Strands wraps the function — the @tool decorator sets tool_name
        assert getattr(get_report, "tool_name", "") == "get_report"


# ---------------------------------------------------------------------------
# Traces — get_report returns per-section trace lists
# ---------------------------------------------------------------------------


class TestTracesInGetReport:
    """Each section in the returned report dict carries a `traces` list
    with the tool calls (input/output/duration/sub-calls) recorded
    during that section's generation. The model can use these to answer
    "how was this report built?" questions without re-running the agent.
    """

    SAMPLE_TRACE = {
        "tool_name": "get_cost_and_usage",
        "duration_s": 1.2,
        "status": "success",
        "input": {"period": "April 2026"},
        "output": '{"total": "$2,995.88"}',
    }

    def test_get_report_returns_traces(self, report_table):
        # Write directly with traces serialized as JSON in the section map,
        # mirroring what reports.save_report produces in production.
        import json as _json
        report_table.put_item(
            TableName=REPORT_TABLE,
            Item={
                "userId": {"S": "report:alice"},
                "templateId": {"S": "report_t1"},
                "title": {"S": "FinOps"},
                "status": {"S": "complete"},
                "month": {"S": "April"},
                "year": {"S": "2026"},
                "createdAt": {"S": "2026-05-01T00:00:00Z"},
                "updatedAt": {"S": "2026-05-01T00:01:00Z"},
                "currentSection": {"N": "1"},
                "totalSections": {"N": "1"},
                "version": {"N": "1"},
                "sections": {
                    "L": [
                        {
                            "M": {
                                "id": {"S": "cost"},
                                "title": {"S": "Cost"},
                                "status": {"S": "complete"},
                                "content": {"S": "body"},
                                "error": {"S": ""},
                                "generated_at": {"S": "2026-05-01T00:00:30Z"},
                                "traces": {"S": _json.dumps([self.SAMPLE_TRACE])},
                            }
                        }
                    ]
                },
            },
        )

        get_report = make_get_report_tool(
            actor_id="alice", region="us-east-1", table_name=REPORT_TABLE
        )
        result = get_report(report_id="report_t1")

        assert "error" not in result
        assert len(result["sections"]) == 1
        assert result["sections"][0]["traces"] == [self.SAMPLE_TRACE]

    def test_section_without_traces_attribute_returns_empty_list(self, report_table):
        # Reports created before traces were persisted (or sections that
        # genuinely had no tool calls) should yield an empty traces list,
        # not a missing key.
        _put_report(
            report_table,
            actor_id="alice",
            report_id="report_old",
            sections=[
                {
                    "id": "s1",
                    "title": "Section One",
                    "status": "complete",
                    "content": "body",
                    "error": "",
                    "generated_at": "2026-05-01T00:00:00Z",
                }
            ],
        )
        get_report = make_get_report_tool(
            actor_id="alice", region="us-east-1", table_name=REPORT_TABLE
        )
        result = get_report(report_id="report_old")
        assert result["sections"][0]["traces"] == []
