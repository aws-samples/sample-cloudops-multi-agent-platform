"""Shared report generation engine.

Handles template loading, dependency-ordered section execution (with parallel
independent sections), DynamoDB persistence, and progress callbacks.

The module is agent-framework-agnostic: callers provide an ``agent_invoke_fn``
callable ``(prompt: str) -> str`` that the engine calls once per section.
The reports module doesn't know about Strands, models, or tools.

Usage::

    from agents.shared.reports import (
        load_template,
        build_dependency_graph,
        generate_report_sections,
        save_report,
        create_report_record,
    )

    template = load_template("finops_monthly_report", actor_id, table, region)
    sections = template["sections"]
    deps = template["dependencies"]

    report = create_report_record(actor_id, template["name"], sections, "January", "2026")
    results = generate_report_sections(
        sections, deps, agent_invoke_fn, {"month": "January", "year": "2026"},
        on_section_complete=lambda idx, status, content, error: ...,
    )
    save_report(report, table, region)
"""

from __future__ import annotations

import glob
import json
import logging
import os
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import boto3

logger = logging.getLogger(__name__)

# Built-in templates directory
_BUILTIN_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "report_templates")

_ddb_client = None


def _get_ddb(region: str = "us-east-1"):
    global _ddb_client
    if _ddb_client is None:
        _ddb_client = boto3.client("dynamodb", region_name=region)
    return _ddb_client


# ---------------------------------------------------------------------------
# Template loading
# ---------------------------------------------------------------------------


def _parse_template_item(item: dict) -> dict:
    """Parse a DynamoDB template item into a plain dict."""
    result = {
        "template_id": item.get("templateId", {}).get("S", ""),
        "user_id": item.get("userId", {}).get("S", ""),
        "name": item.get("name", {}).get("S", ""),
        "description": item.get("description", {}).get("S", ""),
        "created_at": item.get("createdAt", {}).get("S", ""),
        "updated_at": item.get("updatedAt", {}).get("S", ""),
    }
    if "sections" in item:
        try:
            result["sections"] = json.loads(item["sections"].get("S", "[]"))
        except (json.JSONDecodeError, TypeError):
            result["sections"] = []
    if "dependencies" in item:
        try:
            result["dependencies"] = json.loads(item["dependencies"].get("S", "{}"))
        except (json.JSONDecodeError, TypeError):
            result["dependencies"] = {}
    return result


def _load_builtin_templates() -> dict[str, dict]:
    """Load built-in JSON templates from the supervisor's report_templates/ dir.

    Returns a dict keyed by template_id (filename without extension).
    """
    templates: dict[str, dict] = {}
    template_dir = os.path.normpath(_BUILTIN_TEMPLATES_DIR)
    if not os.path.isdir(template_dir):
        return templates
    for path in glob.glob(os.path.join(template_dir, "*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            template_id = os.path.splitext(os.path.basename(path))[0]
            templates[template_id] = {
                "template_id": template_id,
                "user_id": "system",
                "name": data.get("name", template_id),
                "description": data.get("description", ""),
                "sections": data.get("sections", []),
                "dependencies": data.get("dependencies", {}),
            }
        except Exception as exc:
            logger.warning("Failed to load built-in template %s: %s", path, exc)
    return templates


def load_template(
    template_id: str,
    actor_id: str,
    report_table: str,
    region: str = "us-east-1",
) -> dict | None:
    """Load a report template by ID.

    Resolution order:
    1. DynamoDB — ``userId="system"`` (system templates)
    2. DynamoDB — ``userId=<actor_id>`` (user templates)
    3. Built-in JSON files in ``src/agents/supervisor/report_templates/``

    Returns a dict with ``template_id``, ``name``, ``sections``, ``dependencies``
    or ``None`` if not found.
    """
    # Try DynamoDB first
    if report_table and template_id:
        try:
            ddb = _get_ddb(region)
            user_ids_to_try = ["system"]
            if actor_id:
                user_ids_to_try.append(actor_id)

            for uid in user_ids_to_try:
                try:
                    resp = ddb.get_item(
                        TableName=report_table,
                        Key={
                            "userId": {"S": uid},
                            "templateId": {"S": template_id},
                        },
                    )
                    item = resp.get("Item")
                    if item:
                        parsed = _parse_template_item(item)
                        if parsed.get("sections"):
                            logger.info(
                                "Loaded template '%s' from DynamoDB (userId=%s, %d sections)",
                                parsed.get("name", template_id),
                                uid,
                                len(parsed["sections"]),
                            )
                            return parsed
                except Exception:
                    pass
        except Exception as exc:
            logger.error("Failed to query DynamoDB for template: %s", exc)

    # Fallback to built-in JSON files
    builtin = _load_builtin_templates()
    if template_id and template_id in builtin:
        logger.info("Loaded built-in template '%s'", template_id)
        return builtin[template_id]

    # If no specific template_id matched, return the first built-in template
    if builtin:
        first_id = next(iter(builtin))
        logger.info("Using default built-in template '%s' (fallback)", first_id)
        return builtin[first_id]

    return None


# ---------------------------------------------------------------------------
# Dependency graph
# ---------------------------------------------------------------------------


def build_dependency_graph(
    sections: list[dict],
    dependencies: dict[str, str],
) -> list[list[tuple[int, dict]]]:
    """Build execution order from sections and their dependencies.

    Args:
        sections: List of section defs, each with at least an ``id`` key.
        dependencies: Mapping of ``section_id → prerequisite_section_id``.
            A section may depend on at most one other section (single-dep model
            matching the reference project).

    Returns:
        A list of batches. Each batch is a list of ``(index, section_def)``
        tuples that can execute in parallel. Later batches depend on earlier
        ones having completed.
    """
    if not sections:
        return []

    # Map section id → index for quick lookup
    id_to_idx: dict[str, int] = {}
    for i, s in enumerate(sections):
        id_to_idx[s["id"]] = i

    # Build adjacency: prerequisite → list of dependents
    dependents: dict[str, list[str]] = defaultdict(list)
    in_degree: dict[str, int] = {s["id"]: 0 for s in sections}

    for section_id, prereq_id in dependencies.items():
        if section_id in in_degree and prereq_id in in_degree:
            dependents[prereq_id].append(section_id)
            in_degree[section_id] += 1

    # Topological sort via Kahn's algorithm, grouping by batch
    batches: list[list[tuple[int, dict]]] = []
    ready = [sid for sid, deg in in_degree.items() if deg == 0]

    while ready:
        batch = [(id_to_idx[sid], sections[id_to_idx[sid]]) for sid in ready]
        batches.append(batch)

        next_ready: list[str] = []
        for sid in ready:
            for dep_sid in dependents.get(sid, []):
                in_degree[dep_sid] -= 1
                if in_degree[dep_sid] == 0:
                    next_ready.append(dep_sid)
        ready = next_ready

    return batches


# ---------------------------------------------------------------------------
# Section generation
# ---------------------------------------------------------------------------


def generate_report_sections(
    sections: list[dict],
    dependencies: dict[str, str],
    agent_invoke_fn: Callable[[str], str],
    variables: dict[str, str] | None = None,
    on_section_complete: Optional[Callable[[int, str, str, str], None]] = None,
) -> list[dict]:
    """Generate all report sections respecting dependency ordering.

    Independent sections run in parallel via ``ThreadPoolExecutor``.
    Dependent sections run after their prerequisites complete.

    Args:
        sections: Template section definitions (``id``, ``title``, ``prompt``).
        dependencies: ``section_id → prerequisite_section_id`` mapping.
        agent_invoke_fn: ``(prompt: str) -> str`` — called once per section.
        variables: Variable substitution map (e.g. ``{"month": "January", "year": "2026"}``).
            Applied to each section's prompt via ``str.format_map``.
        on_section_complete: Optional callback
            ``(section_idx, status, content, error) -> None`` fired after each
            section finishes (success or failure).

    Returns:
        List of section result dicts, one per input section (same order),
        each with ``id``, ``title``, ``status``, ``content``, ``error``,
        ``generated_at``.
    """
    var_map = defaultdict(str, **(variables or {}))
    batches = build_dependency_graph(sections, dependencies)
    deps_map = dependencies or {}

    # Pre-populate results list
    results: list[dict] = [
        {
            "id": s["id"],
            "title": s["title"],
            "status": "pending",
            "content": "",
            "error": "",
            "generated_at": "",
        }
        for s in sections
    ]
    id_to_idx = {s["id"]: i for i, s in enumerate(sections)}

    def _run_section(idx: int, section_def: dict) -> tuple[int, str, str, str]:
        """Execute a single section. Returns (idx, status, content, error)."""
        try:
            prompt = section_def["prompt"].format_map(var_map)
            content = agent_invoke_fn(prompt)
            return (idx, "complete", content, "")
        except Exception as exc:
            logger.error("Section '%s' failed: %s", section_def.get("id", idx), exc)
            return (idx, "error", "", str(exc))

    def _record_result(idx: int, status: str, content: str, error: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        results[idx]["status"] = status
        results[idx]["content"] = content
        results[idx]["error"] = error
        results[idx]["generated_at"] = now
        if on_section_complete:
            on_section_complete(idx, status, content, error)

    def _prereq_failed(section_def: dict) -> tuple[bool, str]:
        """Check whether this section's prerequisite completed successfully.

        Returns ``(failed, prereq_id)`` so callers can emit an informative
        error for skipped dependents instead of silently dropping them.
        """
        prereq_id = deps_map.get(section_def["id"], "")
        if not prereq_id:
            return (False, "")
        prereq_idx = id_to_idx.get(prereq_id)
        if prereq_idx is None:
            return (False, "")
        prereq_status = results[prereq_idx].get("status", "pending")
        if prereq_status == "error" or prereq_status == "skipped":
            return (True, prereq_id)
        return (False, prereq_id)

    for batch in batches:
        # Partition the batch into (runnable, skipped-because-prereq-failed).
        runnable: list[tuple[int, dict]] = []
        for idx, section_def in batch:
            failed, prereq_id = _prereq_failed(section_def)
            if failed:
                _record_result(
                    idx,
                    "skipped",
                    "",
                    f"Skipped: prerequisite section '{prereq_id}' failed.",
                )
            else:
                runnable.append((idx, section_def))

        if not runnable:
            continue

        if len(runnable) == 1:
            # Single section — run directly, no thread overhead
            idx, section_def = runnable[0]
            idx, status, content, error = _run_section(idx, section_def)
            _record_result(idx, status, content, error)
        else:
            # Multiple independent sections — run in parallel
            max_workers = min(len(runnable), 5)
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(_run_section, idx, section_def): idx
                    for idx, section_def in runnable
                }
                for future in as_completed(futures):
                    try:
                        idx, status, content, error = future.result()
                    except Exception as exc:
                        idx = futures[future]
                        status, content, error = "error", "", str(exc)

                    _record_result(idx, status, content, error)

    return results


# ---------------------------------------------------------------------------
# Report record creation
# ---------------------------------------------------------------------------


def create_report_record(
    actor_id: str,
    template_name: str,
    sections: list[dict],
    month: str,
    year: str,
) -> dict:
    """Create an initial report dict with pending sections.

    This is a pure data-construction function — no I/O. Call ``save_report``
    to persist it to DynamoDB.
    """
    report_id = f"report_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()

    report_sections = [
        {
            "id": s["id"],
            "title": s["title"],
            "status": "pending",
            "content": "",
            "error": "",
            "generated_at": "",
        }
        for s in sections
    ]

    return {
        "report_id": report_id,
        "user_id": actor_id,
        "title": f"{template_name} - {month} {year}",
        "status": "pending",
        "month": month,
        "year": year,
        "created_at": now,
        "updated_at": now,
        "sections": report_sections,
        "current_section": 0,
        "total_sections": len(sections),
        "parent_report_id": "",
        "version": 1,
    }


def create_edit_report_record(
    parent_report: dict,
    edit_prompt: str,
) -> dict:
    """Create a new report record that is version N+1 of ``parent_report``.

    Copies the parent's sections verbatim as a starting point — subsequent
    generation can overwrite any/all of them. Keeps the same title and
    month/year; backs each edit with a new ``report_id`` so previous
    versions stay untouched and clickable in the conversation.

    Args:
        parent_report: The full parent report dict (as returned by
            ``load_report``).
        edit_prompt: The user's edit instruction — stored in
            ``edit_prompt`` so the agent driver can pass it into the
            regeneration call and so consumers can display "edited with
            '…'" context.

    Returns:
        A new report dict with ``parent_report_id`` set to the parent's
        ``report_id`` and ``version`` incremented.
    """
    new_id = f"report_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()
    parent_version = int(parent_report.get("version", 1))

    cloned_sections = []
    for s in parent_report.get("sections", []) or []:
        cloned_sections.append(
            {
                "id": s.get("id", ""),
                "title": s.get("title", ""),
                "status": s.get("status", "pending"),
                "content": s.get("content", ""),
                "error": "",  # clear prior errors; regeneration retries
                "generated_at": s.get("generated_at", ""),
            }
        )

    return {
        "report_id": new_id,
        "user_id": parent_report.get("user_id", ""),
        "title": parent_report.get("title", ""),
        "status": "pending",
        "month": parent_report.get("month", ""),
        "year": parent_report.get("year", ""),
        "created_at": now,
        "updated_at": now,
        "sections": cloned_sections,
        "current_section": 0,
        "total_sections": len(cloned_sections),
        "parent_report_id": parent_report.get("report_id", ""),
        "version": parent_version + 1,
        "edit_prompt": edit_prompt,
    }


def load_report(
    actor_id: str,
    report_id: str,
    report_table: str,
    region: str = "us-east-1",
) -> Optional[dict]:
    """Load a persisted report from DynamoDB — the inverse of ``save_report``.

    Returns a plain dict matching the shape of ``create_report_record``
    output (with ``parent_report_id``/``version`` populated if the row
    has them). Returns ``None`` when the row is missing or malformed.
    """
    if not report_table or not actor_id or not report_id:
        return None
    try:
        ddb = _get_ddb(region)
        resp = ddb.get_item(
            TableName=report_table,
            Key={
                "userId": {"S": f"report:{actor_id}"},
                "templateId": {"S": report_id},
            },
        )
    except Exception as exc:
        logger.error("Failed to load report %s: %s", report_id, exc)
        return None

    item = resp.get("Item")
    if not item:
        return None

    sections: list[dict] = []
    for s_item in item.get("sections", {}).get("L", []):
        sec = s_item.get("M", {})
        traces_str = sec.get("traces", {}).get("S", "")
        try:
            traces = json.loads(traces_str) if traces_str else []
        except (json.JSONDecodeError, TypeError):
            traces = []
        sections.append(
            {
                "id": sec.get("id", {}).get("S", ""),
                "title": sec.get("title", {}).get("S", ""),
                "status": sec.get("status", {}).get("S", ""),
                "content": sec.get("content", {}).get("S", ""),
                "error": sec.get("error", {}).get("S", ""),
                "generated_at": sec.get("generated_at", {}).get("S", ""),
                "traces": traces,
            }
        )

    return {
        "report_id": report_id,
        "user_id": actor_id,
        "title": item.get("title", {}).get("S", ""),
        "status": item.get("status", {}).get("S", ""),
        "month": item.get("month", {}).get("S", ""),
        "year": item.get("year", {}).get("S", ""),
        "created_at": item.get("createdAt", {}).get("S", ""),
        "updated_at": item.get("updatedAt", {}).get("S", ""),
        "sections": sections,
        "current_section": int(item.get("currentSection", {}).get("N", "0") or 0),
        "total_sections": int(item.get("totalSections", {}).get("N", "0") or 0),
        "parent_report_id": item.get("parentReportId", {}).get("S", ""),
        "version": int(item.get("version", {}).get("N", "1") or 1),
    }


# ---------------------------------------------------------------------------
# DynamoDB persistence
# ---------------------------------------------------------------------------


def save_report(
    report_data: dict,
    report_table: str,
    region: str = "us-east-1",
) -> bool:
    """Persist a report to DynamoDB.

    Uses ``userId=report:{actor_id}`` as the partition key and
    ``templateId={report_id}`` as the sort key — matching the schema that
    the frontend Lambda handler (``_parse_report_item``, ``_list_reports``)
    expects.

    Args:
        report_data: Report dict as returned by ``create_report_record``
            (with sections potentially updated by ``generate_report_sections``).
        report_table: DynamoDB table name.
        region: AWS region.

    Returns:
        True on success, False on failure.
    """
    if not report_table:
        logger.warning("report_table not set — cannot save report")
        return False

    try:
        ddb = _get_ddb(region)

        # Convert sections to DynamoDB list-of-maps format. Traces are
        # JSON-encoded as a single string per section because (a) the
        # nested toolUse/toolResult shape is irregular and changes as the
        # tracing pipeline evolves, and (b) we never query traces from
        # DDB — they round-trip whole on every read.
        from agents.shared.redact import redact

        sections_data: list[dict] = []
        for s in report_data.get("sections", []):
            section_map: dict = {
                "id": {"S": s.get("id", "")},
                "title": {"S": s.get("title", "")},
                "status": {"S": s.get("status", "pending")},
                "content": {"S": redact(s.get("content", ""))},
                "error": {"S": s.get("error", "")},
                "generated_at": {"S": s.get("generated_at", "")},
            }
            traces = s.get("traces") or []
            if traces:
                try:
                    section_map["traces"] = {"S": json.dumps(traces, default=str)}
                except (TypeError, ValueError):
                    pass
            sections_data.append({"M": section_map})

        item: dict = {
            "userId": {"S": f"report:{report_data['user_id']}"},
            "templateId": {"S": report_data["report_id"]},
            "title": {"S": report_data.get("title", "")},
            "status": {"S": report_data.get("status", "pending")},
            "month": {"S": report_data.get("month", "")},
            "year": {"S": report_data.get("year", "")},
            "createdAt": {"S": report_data.get("created_at", "")},
            "updatedAt": {"S": report_data.get("updated_at", "")},
            "sections": {"L": sections_data},
            "currentSection": {"N": str(report_data.get("current_section", 0))},
            "totalSections": {"N": str(report_data.get("total_sections", 0))},
            "version": {"N": str(int(report_data.get("version", 1)))},
        }
        parent_id = report_data.get("parent_report_id", "")
        if parent_id:
            item["parentReportId"] = {"S": parent_id}
        edit_prompt = report_data.get("edit_prompt", "")
        if edit_prompt:
            item["editPrompt"] = {"S": edit_prompt}
        ddb.put_item(TableName=report_table, Item=item)
        logger.info(
            "Saved report %s for user %s",
            report_data.get("report_id"),
            report_data.get("user_id"),
        )
        return True
    except Exception as exc:
        logger.error("Failed to save report: %s", exc)
        return False


def update_report_section(
    actor_id: str,
    report_id: str,
    section_idx: int,
    section: dict,
    current_section: int,
    report_table: str,
    region: str = "us-east-1",
    status: str | None = None,
) -> bool:
    """Targeted update of a single section inside a persisted report.

    Writes ``sections[section_idx] = section``, bumps ``currentSection``
    to the caller-supplied count, stamps ``updatedAt``, and optionally
    updates the top-level report status (e.g. ``in_progress`` → ``complete``).

    Uses DynamoDB's list-index update expression so other sections and
    metadata remain untouched — makes per-section persistence cheap.
    Intended to be called from the ``on_section_complete`` callback of
    ``generate_report_sections`` so a crash mid-report doesn't lose
    already-finished sections.
    """
    if not report_table or not report_id or not actor_id:
        logger.warning("update_report_section: missing required args")
        return False

    try:
        ddb = _get_ddb(region)
        inner: dict = {
            "id": {"S": section.get("id", "")},
            "title": {"S": section.get("title", "")},
            "status": {"S": section.get("status", "pending")},
            "content": {"S": section.get("content", "")},
            "error": {"S": section.get("error", "")},
            "generated_at": {"S": section.get("generated_at", "")},
        }
        traces = section.get("traces") or []
        if traces:
            try:
                inner["traces"] = {"S": json.dumps(traces, default=str)}
            except (TypeError, ValueError):
                pass
        section_m = {"M": inner}
        now = datetime.now(timezone.utc).isoformat()

        update_expr = (
            f"SET sections[{int(section_idx)}] = :sec, "
            f"currentSection = :cs, "
            f"updatedAt = :u"
        )
        expr_values: dict = {
            ":sec": section_m,
            ":cs": {"N": str(int(current_section))},
            ":u": {"S": now},
        }
        expr_names: dict = {}
        if status:
            update_expr += ", #st = :st"
            expr_names["#st"] = "status"
            expr_values[":st"] = {"S": status}

        kwargs: dict = {
            "TableName": report_table,
            "Key": {
                "userId": {"S": f"report:{actor_id}"},
                "templateId": {"S": report_id},
            },
            "UpdateExpression": update_expr,
            "ExpressionAttributeValues": expr_values,
        }
        if expr_names:
            kwargs["ExpressionAttributeNames"] = expr_names

        ddb.update_item(**kwargs)
        return True
    except Exception as exc:
        logger.error(
            "Failed to update section %s of report %s: %s",
            section_idx,
            report_id,
            exc,
        )
        return False
