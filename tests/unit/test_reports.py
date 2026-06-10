"""Unit tests for agents.shared.reports module."""

import json
import os
import threading
import time

import boto3
import pytest
from moto import mock_aws

from agents.shared.reports import (
    build_dependency_graph,
    create_edit_report_record,
    create_report_record,
    generate_report_sections,
    load_report,
    load_template,
    save_report,
    update_report_section,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_SECTIONS = [
    {
        "id": "cost_overview",
        "title": "Cost Overview",
        "prompt": "Cost for {month} {year}",
    },
    {
        "id": "governance",
        "title": "Governance",
        "prompt": "Governance for {month} {year}",
    },
    {
        "id": "ops_health",
        "title": "Ops Health",
        "prompt": "Ops health for {month} {year}",
    },
    {"id": "security", "title": "Security", "prompt": "Security for {month} {year}"},
]

SAMPLE_DEPS = {"security": "governance"}

REPORT_TABLE = "test-reports"


@pytest.fixture
def report_table(mock_aws_env):
    """Create the reports DynamoDB table matching the frontend handler schema."""
    import agents.shared.reports as reports_mod

    reports_mod._ddb_client = None  # Reset cached client

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


# ---------------------------------------------------------------------------
# build_dependency_graph tests
# ---------------------------------------------------------------------------


class TestBuildDependencyGraph:
    def test_no_sections(self):
        assert build_dependency_graph([], {}) == []

    def test_all_independent(self):
        """All sections with no deps should be in a single batch."""
        sections = [
            {"id": "a", "title": "A", "prompt": "..."},
            {"id": "b", "title": "B", "prompt": "..."},
            {"id": "c", "title": "C", "prompt": "..."},
        ]
        batches = build_dependency_graph(sections, {})
        assert len(batches) == 1
        ids_in_batch = {s["id"] for _, s in batches[0]}
        assert ids_in_batch == {"a", "b", "c"}

    def test_single_dependency(self):
        """security depends on governance → two batches."""
        batches = build_dependency_graph(SAMPLE_SECTIONS, SAMPLE_DEPS)
        # First batch: everything except security
        first_ids = {s["id"] for _, s in batches[0]}
        assert "security" not in first_ids
        assert "governance" in first_ids
        # Security must be in a later batch
        later_ids = set()
        for batch in batches[1:]:
            for _, s in batch:
                later_ids.add(s["id"])
        assert "security" in later_ids

    def test_chain_dependency(self):
        """a → b → c should produce 3 batches."""
        sections = [
            {"id": "a", "title": "A", "prompt": "..."},
            {"id": "b", "title": "B", "prompt": "..."},
            {"id": "c", "title": "C", "prompt": "..."},
        ]
        deps = {"b": "a", "c": "b"}
        batches = build_dependency_graph(sections, deps)
        assert len(batches) == 3
        assert batches[0][0][1]["id"] == "a"
        assert batches[1][0][1]["id"] == "b"
        assert batches[2][0][1]["id"] == "c"

    def test_preserves_section_index(self):
        """Returned tuples should carry the original section index."""
        sections = [
            {"id": "x", "title": "X", "prompt": "..."},
            {"id": "y", "title": "Y", "prompt": "..."},
        ]
        batches = build_dependency_graph(sections, {"y": "x"})
        # x is at index 0, y at index 1
        assert batches[0][0][0] == 0
        assert batches[1][0][0] == 1

    def test_ignores_unknown_dependency(self):
        """Dependencies referencing non-existent sections are ignored."""
        sections = [{"id": "a", "title": "A", "prompt": "..."}]
        deps = {"a": "nonexistent"}
        batches = build_dependency_graph(sections, deps)
        # 'a' depends on 'nonexistent' which isn't in sections, so dep is ignored
        assert len(batches) == 1
        assert batches[0][0][1]["id"] == "a"


# ---------------------------------------------------------------------------
# generate_report_sections tests
# ---------------------------------------------------------------------------


class TestGenerateReportSections:
    def test_basic_generation(self):
        """All sections should be generated with variable substitution."""

        def mock_invoke(prompt: str) -> str:
            return f"Result for: {prompt}"

        results = generate_report_sections(
            SAMPLE_SECTIONS,
            SAMPLE_DEPS,
            mock_invoke,
            {"month": "January", "year": "2026"},
        )
        assert len(results) == 4
        for r in results:
            assert r["status"] == "complete"
            assert "January" in r["content"]
            assert "2026" in r["content"]
            assert r["generated_at"] != ""

    def test_section_failure_captured(self):
        """A failing section should have status='error' and error message."""

        def failing_invoke(prompt: str) -> str:
            if "Governance" in prompt:
                raise RuntimeError("API timeout")
            return "ok"

        results = generate_report_sections(
            [
                {"id": "a", "title": "A", "prompt": "A for {month}"},
                {"id": "b", "title": "Governance", "prompt": "Governance for {month}"},
            ],
            {},
            failing_invoke,
            {"month": "Jan"},
        )
        assert results[0]["status"] == "complete"
        assert results[1]["status"] == "error"
        assert "API timeout" in results[1]["error"]

    def test_callback_fired(self):
        """on_section_complete should be called for each section."""
        callback_log = []

        def mock_invoke(prompt: str) -> str:
            return "done"

        def on_complete(idx, status, content, error):
            callback_log.append((idx, status))

        generate_report_sections(SAMPLE_SECTIONS[:2], {}, mock_invoke, {}, on_complete)
        assert len(callback_log) == 2
        assert all(s == "complete" for _, s in callback_log)

    def test_parallel_execution(self):
        """Independent sections should run in parallel, not sequentially."""
        start_times = {}
        lock = threading.Lock()

        def slow_invoke(prompt: str) -> str:
            tid = threading.current_thread().ident
            with lock:
                start_times[prompt] = time.monotonic()
            time.sleep(0.2)
            return "done"

        sections = [
            {"id": "a", "title": "A", "prompt": "prompt_a"},
            {"id": "b", "title": "B", "prompt": "prompt_b"},
            {"id": "c", "title": "C", "prompt": "prompt_c"},
        ]

        t0 = time.monotonic()
        results = generate_report_sections(sections, {}, slow_invoke, {})
        elapsed = time.monotonic() - t0

        # 3 sections × 0.2s each. If sequential: ~0.6s. If parallel: ~0.2s.
        # Allow generous margin but confirm it's faster than sequential.
        assert elapsed < 0.5, f"Sections ran sequentially ({elapsed:.2f}s)"
        assert all(r["status"] == "complete" for r in results)

    def test_dependency_ordering(self):
        """Dependent sections must run after their prerequisites."""
        execution_order = []
        lock = threading.Lock()

        def tracking_invoke(prompt: str) -> str:
            with lock:
                execution_order.append(prompt)
            return "done"

        sections = [
            {"id": "a", "title": "A", "prompt": "prompt_a"},
            {"id": "b", "title": "B", "prompt": "prompt_b"},
        ]
        deps = {"b": "a"}

        generate_report_sections(sections, deps, tracking_invoke, {})
        assert execution_order.index("prompt_a") < execution_order.index("prompt_b")

    def test_dependent_skipped_when_prereq_fails(self):
        """If a prereq errors, dependents must be skipped with status=skipped
        and a pointer to the failed prereq — not silently executed."""
        invoked_prompts = []

        def invoke(prompt: str) -> str:
            invoked_prompts.append(prompt)
            if "prereq" in prompt:
                raise RuntimeError("boom")
            return "ok"

        sections = [
            {"id": "a", "title": "A", "prompt": "prereq"},
            {"id": "b", "title": "B", "prompt": "dependent"},
        ]
        deps = {"b": "a"}

        results = generate_report_sections(sections, deps, invoke, {})
        assert results[0]["status"] == "error"
        assert results[1]["status"] == "skipped"
        assert "'a'" in results[1]["error"]
        # The dependent's prompt must NOT have been sent to the LLM.
        assert "dependent" not in invoked_prompts

    def test_callback_fired_for_skipped_dependents(self):
        """on_section_complete must still fire for skipped sections so the
        caller (agui_server) can emit a progress event or persist the row."""
        callback_log: list[tuple[int, str]] = []

        def invoke(prompt: str) -> str:
            if "prereq" in prompt:
                raise RuntimeError("boom")
            return "ok"

        def on_complete(idx, status, content, error):
            callback_log.append((idx, status))

        sections = [
            {"id": "a", "title": "A", "prompt": "prereq"},
            {"id": "b", "title": "B", "prompt": "dependent"},
            {"id": "c", "title": "C", "prompt": "also dependent"},
        ]
        deps = {"b": "a", "c": "a"}

        generate_report_sections(sections, deps, invoke, {}, on_complete)
        # All three sections yielded a status — one error, two skipped
        assert len(callback_log) == 3
        statuses = {status for _, status in callback_log}
        assert statuses == {"error", "skipped"}


# ---------------------------------------------------------------------------
# create_report_record tests
# ---------------------------------------------------------------------------


class TestCreateReportRecord:
    def test_creates_valid_record(self):
        record = create_report_record(
            "user123", "Monthly Report", SAMPLE_SECTIONS, "January", "2026"
        )
        assert record["report_id"].startswith("report_")
        assert record["user_id"] == "user123"
        assert record["title"] == "Monthly Report - January 2026"
        assert record["status"] == "pending"
        assert record["month"] == "January"
        assert record["year"] == "2026"
        assert record["total_sections"] == 4
        assert record["current_section"] == 0
        assert len(record["sections"]) == 4
        for s in record["sections"]:
            assert s["status"] == "pending"
            assert s["content"] == ""

    def test_unique_report_ids(self):
        r1 = create_report_record("u", "T", SAMPLE_SECTIONS, "Jan", "2026")
        r2 = create_report_record("u", "T", SAMPLE_SECTIONS, "Jan", "2026")
        assert r1["report_id"] != r2["report_id"]


# ---------------------------------------------------------------------------
# save_report + DynamoDB schema tests
# ---------------------------------------------------------------------------


class TestSaveReport:
    def test_save_and_read_back(self, report_table):
        """Saved report should be readable by the frontend handler's schema."""
        record = create_report_record(
            "user_at_example", "Test Report", SAMPLE_SECTIONS[:2], "Feb", "2026"
        )
        record["status"] = "complete"
        record["sections"][0]["status"] = "complete"
        record["sections"][0]["content"] = "Section 1 content"

        ok = save_report(record, REPORT_TABLE, "us-east-1")
        assert ok is True

        # Read back using the same key pattern the frontend handler uses
        resp = report_table.get_item(
            TableName=REPORT_TABLE,
            Key={
                "userId": {"S": f"report:{record['user_id']}"},
                "templateId": {"S": record["report_id"]},
            },
        )
        item = resp["Item"]
        assert item["userId"]["S"] == "report:user_at_example"
        assert item["templateId"]["S"] == record["report_id"]
        assert item["title"]["S"] == "Test Report - Feb 2026"
        assert item["status"]["S"] == "complete"
        assert item["month"]["S"] == "Feb"
        assert item["year"]["S"] == "2026"
        assert int(item["totalSections"]["N"]) == 2

        # Verify sections list structure matches _parse_report_item expectations
        sections_list = item["sections"]["L"]
        assert len(sections_list) == 2
        first = sections_list[0]["M"]
        assert first["id"]["S"] == "cost_overview"
        assert first["status"]["S"] == "complete"
        assert first["content"]["S"] == "Section 1 content"

    def test_save_without_table_returns_false(self):
        ok = save_report({}, "", "us-east-1")
        assert ok is False


# ---------------------------------------------------------------------------
# update_report_section tests
# ---------------------------------------------------------------------------


class TestUpdateReportSection:
    def test_targeted_update_preserves_other_sections(self, report_table):
        """Updating section 1 must not clobber section 0 or top-level fields."""
        record = create_report_record(
            "alice", "Test", SAMPLE_SECTIONS[:3], "Feb", "2026"
        )
        record["status"] = "in_progress"
        save_report(record, REPORT_TABLE, "us-east-1")

        updated_section = {
            "id": record["sections"][1]["id"],
            "title": record["sections"][1]["title"],
            "status": "complete",
            "content": "Filled in content for section 1",
            "error": "",
            "generated_at": "2026-02-01T00:00:00+00:00",
        }
        ok = update_report_section(
            "alice",
            record["report_id"],
            section_idx=1,
            section=updated_section,
            current_section=2,
            report_table=REPORT_TABLE,
            region="us-east-1",
        )
        assert ok is True

        resp = report_table.get_item(
            TableName=REPORT_TABLE,
            Key={
                "userId": {"S": "report:alice"},
                "templateId": {"S": record["report_id"]},
            },
        )
        item = resp["Item"]
        # Section 0 still pending (untouched)
        assert item["sections"]["L"][0]["M"]["status"]["S"] == "pending"
        # Section 1 now complete with new content
        assert item["sections"]["L"][1]["M"]["status"]["S"] == "complete"
        assert (
            item["sections"]["L"][1]["M"]["content"]["S"]
            == "Filled in content for section 1"
        )
        # currentSection bumped
        assert int(item["currentSection"]["N"]) == 2
        # Top-level status unchanged (we didn't pass one)
        assert item["status"]["S"] == "in_progress"

    def test_status_override_applied_when_passed(self, report_table):
        record = create_report_record(
            "bob", "Test", SAMPLE_SECTIONS[:1], "Mar", "2026"
        )
        record["status"] = "in_progress"
        save_report(record, REPORT_TABLE, "us-east-1")

        update_report_section(
            "bob",
            record["report_id"],
            section_idx=0,
            section={
                "id": record["sections"][0]["id"],
                "title": record["sections"][0]["title"],
                "status": "complete",
                "content": "all done",
                "error": "",
                "generated_at": "2026-03-01T00:00:00+00:00",
            },
            current_section=1,
            report_table=REPORT_TABLE,
            region="us-east-1",
            status="complete",
        )

        resp = report_table.get_item(
            TableName=REPORT_TABLE,
            Key={
                "userId": {"S": "report:bob"},
                "templateId": {"S": record["report_id"]},
            },
        )
        assert resp["Item"]["status"]["S"] == "complete"

    def test_missing_table_returns_false(self):
        ok = update_report_section(
            "u",
            "r",
            section_idx=0,
            section={},
            current_section=0,
            report_table="",
            region="us-east-1",
        )
        assert ok is False

    def test_missing_ids_return_false(self):
        ok = update_report_section(
            "",
            "r",
            section_idx=0,
            section={},
            current_section=0,
            report_table="t",
            region="us-east-1",
        )
        assert ok is False


# ---------------------------------------------------------------------------
# load_report + versioning tests
# ---------------------------------------------------------------------------


class TestLoadReport:
    def test_save_and_load_roundtrip(self, report_table):
        """Writing with save_report then reading with load_report should
        return an equivalent dict — ensures schema stability for the
        edit flow that re-uses parent content."""
        record = create_report_record(
            "dana", "Test", SAMPLE_SECTIONS[:2], "Apr", "2026"
        )
        record["status"] = "complete"
        record["sections"][0]["status"] = "complete"
        record["sections"][0]["content"] = "Hello world"
        save_report(record, REPORT_TABLE, "us-east-1")

        loaded = load_report("dana", record["report_id"], REPORT_TABLE, "us-east-1")
        assert loaded is not None
        assert loaded["report_id"] == record["report_id"]
        assert loaded["user_id"] == "dana"
        assert loaded["status"] == "complete"
        assert loaded["version"] == 1
        assert loaded["parent_report_id"] == ""
        assert len(loaded["sections"]) == 2
        assert loaded["sections"][0]["content"] == "Hello world"

    def test_load_missing_report_returns_none(self, report_table):
        assert load_report("ghost", "nonexistent", REPORT_TABLE, "us-east-1") is None

    def test_load_without_table_returns_none(self):
        assert load_report("u", "r", "", "us-east-1") is None


class TestCreateEditReportRecord:
    def test_version_increment_and_parent_link(self):
        parent = create_report_record(
            "alice", "Tmpl", SAMPLE_SECTIONS[:2], "May", "2026"
        )
        parent["sections"][0]["content"] = "Section 0 body"
        parent["sections"][0]["status"] = "complete"

        child = create_edit_report_record(parent, "make section 0 shorter")
        assert child["parent_report_id"] == parent["report_id"]
        assert child["version"] == parent["version"] + 1
        assert child["report_id"] != parent["report_id"]
        assert child["title"] == parent["title"]
        # Sections are cloned from the parent as a starting point.
        assert len(child["sections"]) == len(parent["sections"])
        assert child["sections"][0]["content"] == "Section 0 body"
        # The edit prompt is carried for the regeneration call.
        assert child["edit_prompt"] == "make section 0 shorter"
        # Prior errors are cleared — a regen is a clean slate.
        assert child["sections"][0]["error"] == ""

    def test_edit_of_edit_keeps_incrementing(self):
        v1 = create_report_record("bob", "T", SAMPLE_SECTIONS[:1], "Jun", "2026")
        v2 = create_edit_report_record(v1, "first edit")
        v3 = create_edit_report_record(v2, "second edit")
        assert v2["version"] == 2
        assert v3["version"] == 3
        # Each edit points at its immediate parent, not at the root.
        assert v3["parent_report_id"] == v2["report_id"]


class TestSaveReportVersionFields:
    def test_persists_parent_id_and_version(self, report_table):
        parent = create_report_record(
            "carl", "T", SAMPLE_SECTIONS[:1], "Jul", "2026"
        )
        save_report(parent, REPORT_TABLE, "us-east-1")
        child = create_edit_report_record(parent, "tighten it up")
        save_report(child, REPORT_TABLE, "us-east-1")

        # Read the raw DynamoDB item back to confirm the new fields landed.
        resp = report_table.get_item(
            TableName=REPORT_TABLE,
            Key={
                "userId": {"S": "report:carl"},
                "templateId": {"S": child["report_id"]},
            },
        )
        item = resp["Item"]
        assert item["parentReportId"]["S"] == parent["report_id"]
        assert int(item["version"]["N"]) == 2
        assert item["editPrompt"]["S"] == "tighten it up"


# ---------------------------------------------------------------------------
# load_template tests
# ---------------------------------------------------------------------------


class TestLoadTemplate:
    def test_load_builtin_template(self, report_table):
        """Should fall back to built-in JSON when DynamoDB has nothing."""
        template = load_template(
            "finops_monthly_report", "user1", REPORT_TABLE, "us-east-1"
        )
        assert template is not None
        assert template["name"] == "FinOps Monthly Report"
        assert len(template["sections"]) > 0
        assert "dependencies" in template

    def test_load_from_dynamodb_system(self, report_table):
        """System templates in DynamoDB should be found first."""
        report_table.put_item(
            TableName=REPORT_TABLE,
            Item={
                "userId": {"S": "system"},
                "templateId": {"S": "custom_tmpl"},
                "name": {"S": "Custom System Template"},
                "sections": {
                    "S": json.dumps([{"id": "s1", "title": "S1", "prompt": "p1"}])
                },
                "dependencies": {"S": "{}"},
                "createdAt": {"S": "2026-01-01"},
                "updatedAt": {"S": "2026-01-01"},
            },
        )
        template = load_template("custom_tmpl", "user1", REPORT_TABLE, "us-east-1")
        assert template is not None
        assert template["name"] == "Custom System Template"
        assert len(template["sections"]) == 1

    def test_load_from_dynamodb_user(self, report_table):
        """User templates should be found when system template doesn't exist."""
        report_table.put_item(
            TableName=REPORT_TABLE,
            Item={
                "userId": {"S": "user1"},
                "templateId": {"S": "user_tmpl"},
                "name": {"S": "User Template"},
                "sections": {
                    "S": json.dumps([{"id": "u1", "title": "U1", "prompt": "up1"}])
                },
                "dependencies": {"S": "{}"},
                "createdAt": {"S": "2026-01-01"},
                "updatedAt": {"S": "2026-01-01"},
            },
        )
        template = load_template("user_tmpl", "user1", REPORT_TABLE, "us-east-1")
        assert template is not None
        assert template["name"] == "User Template"

    def test_nonexistent_template_falls_back(self, report_table):
        """Non-existent template_id should fall back to built-in."""
        template = load_template("nonexistent_id", "user1", REPORT_TABLE, "us-east-1")
        # Should get the built-in template as fallback
        assert template is not None
        assert len(template["sections"]) > 0


# ---------------------------------------------------------------------------
# Tool trace round-trip — traces persist alongside section content
# ---------------------------------------------------------------------------


class TestSectionTracesRoundTrip:
    """Traces capture HOW a section was generated (which tools the agent
    called, with what inputs, what they returned). They live alongside
    the section content in DDB so ReportPanel can show an Agent Trace
    card without depending on AgentCore Memory survival.
    """

    SAMPLE_TRACE = {
        "tool_name": "get_cost_and_usage",
        "duration_s": 1.34,
        "status": "success",
        "input": {"start_date": "2026-04-01", "end_date": "2026-04-30"},
        "output": '{"results": [{"service": "EC2", "amount": "100.00"}]}',
        "tool_trace": [
            {
                "tool_name": "describe_account",
                "duration_s": 0.21,
                "status": "success",
                "input": {},
                "output": "account-id-here",
            }
        ],
    }

    def test_save_then_load_preserves_traces(self, report_table):
        record = create_report_record(
            "alice", "T", SAMPLE_SECTIONS[:1], "Jul", "2026"
        )
        record["sections"][0]["content"] = "Some markdown body"
        record["sections"][0]["status"] = "complete"
        record["sections"][0]["traces"] = [self.SAMPLE_TRACE]

        save_report(record, REPORT_TABLE, "us-east-1")
        loaded = load_report("alice", record["report_id"], REPORT_TABLE, "us-east-1")

        assert loaded is not None
        assert len(loaded["sections"]) == 1
        assert loaded["sections"][0]["traces"] == [self.SAMPLE_TRACE]

    def test_section_without_traces_loads_as_empty_list(self, report_table):
        # Backwards compat with old rows that never had a `traces` field.
        record = create_report_record(
            "bob", "T", SAMPLE_SECTIONS[:1], "Jul", "2026"
        )
        record["sections"][0]["content"] = "old"
        # Deliberately leave traces unset so save_report doesn't include the field.
        save_report(record, REPORT_TABLE, "us-east-1")

        loaded = load_report("bob", record["report_id"], REPORT_TABLE, "us-east-1")
        assert loaded["sections"][0]["traces"] == []

    def test_update_report_section_persists_traces(self, report_table):
        # The per-section incremental write path must also preserve traces.
        record = create_report_record(
            "carol", "T", SAMPLE_SECTIONS[:2], "Jul", "2026"
        )
        save_report(record, REPORT_TABLE, "us-east-1")

        update_report_section(
            "carol",
            record["report_id"],
            section_idx=0,
            section={
                "id": "cost_overview",
                "title": "Cost Overview",
                "status": "complete",
                "content": "body",
                "error": "",
                "generated_at": "2026-07-01T00:00:00Z",
                "traces": [self.SAMPLE_TRACE],
            },
            current_section=1,
            report_table=REPORT_TABLE,
            region="us-east-1",
        )

        loaded = load_report(
            "carol", record["report_id"], REPORT_TABLE, "us-east-1"
        )
        assert loaded["sections"][0]["traces"] == [self.SAMPLE_TRACE]
        # The other section we never updated stays trace-free.
        assert loaded["sections"][1]["traces"] == []

    def test_traces_with_unicode_and_long_output_serialize(self, report_table):
        # Real tool outputs include unicode (currency symbols, emoji), and
        # tool traces can be tens of KB after _smart_truncate. Ensure
        # json.dumps handles both.
        big_output = "💰 result line\n" * 200
        trace = {
            "tool_name": "get_cost_forecast",
            "duration_s": 2.5,
            "status": "success",
            "input": {"granularity": "MONTHLY"},
            "output": big_output,
        }
        record = create_report_record(
            "dave", "T", SAMPLE_SECTIONS[:1], "Jul", "2026"
        )
        record["sections"][0]["traces"] = [trace]
        save_report(record, REPORT_TABLE, "us-east-1")

        loaded = load_report("dave", record["report_id"], REPORT_TABLE, "us-east-1")
        assert loaded["sections"][0]["traces"][0]["output"] == big_output
