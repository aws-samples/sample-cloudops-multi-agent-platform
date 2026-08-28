"""
AWS Lambda Runtime Upgrade MCP Tool — Lambda Implementation for AgentCore Gateway

Provides tools for discovering Lambda functions with outdated runtimes,
retrieving function code for compatibility analysis, and checking runtime
support/deprecation status.

Tools (8):
- generate_upgrade_analysis: ONE-SHOT aggregator — discovers regions, scans in
  parallel, fetches code for top functions, and runs deterministic code
  compatibility analysis, all in a single call. Preferred fast path for reports.
- discover_lambda_regions: Find all regions that have Lambda functions (with counts)
- list_functions_by_runtime: List Lambda functions filtered by runtime/region
- get_function_configuration: Get detailed function config (runtime, layers, handler)
- get_function_code: Download and return function source code for analysis
- get_runtime_support_status: Show all Lambda runtimes with deprecation/EOL dates
- get_deprecated_functions: Find all functions using deprecated or EOL runtimes
- get_deprecated_functions_multi_region: Scan multiple regions in parallel for deprecated functions

Design note:
  The deterministic work (region discovery, function scanning, code download,
  and breaking-change detection) runs entirely in Python here — in parallel —
  so the agent makes ONE tool call and receives a complete, pre-analyzed
  dataset. The LLM then spends its cycles on judgment (prioritization narrative,
  effort estimates, migration playbook) rather than orchestrating many
  sequential tool calls. This is the primary report-speed optimization.

Required IAM Permissions:
- lambda:ListFunctions
- lambda:GetFunction
- lambda:GetFunctionConfiguration
- ec2:DescribeRegions
"""

import json
import os
import re
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from io import BytesIO
from typing import Optional

import boto3
import urllib3


def handler(event, context):
    print(f"Event: {json.dumps(event)}")
    extended_tool_name = context.client_context.custom["bedrockAgentCoreToolName"]
    tool_name = extended_tool_name.split("___")[1]
    print(f"Tool name: {tool_name}")

    handlers = {
        "generate_upgrade_analysis": handle_generate_upgrade_analysis,
        "discover_lambda_regions": handle_discover_lambda_regions,
        "list_functions_by_runtime": handle_list_functions_by_runtime,
        "get_function_configuration": handle_get_function_configuration,
        "get_function_code": handle_get_function_code,
        "get_runtime_support_status": handle_get_runtime_support_status,
        "get_deprecated_functions": handle_get_deprecated_functions,
        "get_deprecated_functions_multi_region": handle_get_deprecated_functions_multi_region,
    }
    fn = handlers.get(tool_name)
    if fn:
        response = fn(event)
        print(f"Response: {json.dumps(response, default=str)}")
        return response
    return {
        "error": f"Unknown tool: {tool_name}",
        "available_tools": list(handlers.keys()),
    }


# ---------------------------------------------------------------------------
# Runtime support status data (updated periodically)
# NOTE: Refresh cadence — review these dates quarterly against the AWS Lambda
# runtimes page (https://docs.aws.amazon.com/lambda/latest/dg/lambda-runtimes.html).
# Last reviewed: 2025-06. Stale dates won't cause errors but may under-report
# deprecation urgency until refreshed.
# ---------------------------------------------------------------------------

RUNTIME_SUPPORT_DATA = {
    # Python runtimes
    "python3.8": {"family": "python", "version": "3.8", "status": "deprecated",
                  "deprecation_date": "2024-10-14", "eol_date": "2025-02-28",
                  "upgrade_target": "python3.12"},
    "python3.9": {"family": "python", "version": "3.9", "status": "active",
                  "deprecation_date": "2025-09-01", "eol_date": None,
                  "upgrade_target": "python3.12"},
    "python3.10": {"family": "python", "version": "3.10", "status": "active",
                   "deprecation_date": "2026-06-01", "eol_date": None,
                   "upgrade_target": "python3.13"},
    "python3.11": {"family": "python", "version": "3.11", "status": "active",
                   "deprecation_date": "2026-12-01", "eol_date": None,
                   "upgrade_target": "python3.13"},
    "python3.12": {"family": "python", "version": "3.12", "status": "active",
                   "deprecation_date": None, "eol_date": None,
                   "upgrade_target": "python3.13"},
    "python3.13": {"family": "python", "version": "3.13", "status": "active",
                   "deprecation_date": None, "eol_date": None,
                   "upgrade_target": None},
    # Node.js runtimes
    "nodejs14.x": {"family": "nodejs", "version": "14", "status": "deprecated",
                   "deprecation_date": "2023-12-04", "eol_date": "2024-03-11",
                   "upgrade_target": "nodejs20.x"},
    "nodejs16.x": {"family": "nodejs", "version": "16", "status": "deprecated",
                   "deprecation_date": "2024-06-12", "eol_date": "2024-09-11",
                   "upgrade_target": "nodejs20.x"},
    "nodejs18.x": {"family": "nodejs", "version": "18", "status": "active",
                   "deprecation_date": "2025-09-01", "eol_date": None,
                   "upgrade_target": "nodejs22.x"},
    "nodejs20.x": {"family": "nodejs", "version": "20", "status": "active",
                   "deprecation_date": "2026-06-01", "eol_date": None,
                   "upgrade_target": "nodejs22.x"},
    "nodejs22.x": {"family": "nodejs", "version": "22", "status": "active",
                   "deprecation_date": None, "eol_date": None,
                   "upgrade_target": None},
    # Java runtimes
    "java8": {"family": "java", "version": "8", "status": "deprecated",
              "deprecation_date": "2024-01-08", "eol_date": "2024-04-08",
              "upgrade_target": "java21"},
    "java8.al2": {"family": "java", "version": "8 (AL2)", "status": "deprecated",
                  "deprecation_date": "2024-08-01", "eol_date": "2024-11-01",
                  "upgrade_target": "java21"},
    "java11": {"family": "java", "version": "11", "status": "active",
               "deprecation_date": "2025-09-01", "eol_date": None,
               "upgrade_target": "java21"},
    "java17": {"family": "java", "version": "17", "status": "active",
               "deprecation_date": "2026-09-01", "eol_date": None,
               "upgrade_target": "java21"},
    "java21": {"family": "java", "version": "21", "status": "active",
               "deprecation_date": None, "eol_date": None,
               "upgrade_target": None},
    # .NET runtimes
    "dotnet6": {"family": "dotnet", "version": "6", "status": "deprecated",
                "deprecation_date": "2024-02-29", "eol_date": "2024-05-29",
                "upgrade_target": "dotnet8"},
    "dotnet8": {"family": "dotnet", "version": "8", "status": "active",
                "deprecation_date": None, "eol_date": None,
                "upgrade_target": None},
    # Ruby runtimes
    "ruby3.2": {"family": "ruby", "version": "3.2", "status": "active",
                "deprecation_date": "2026-03-01", "eol_date": None,
                "upgrade_target": "ruby3.3"},
    "ruby3.3": {"family": "ruby", "version": "3.3", "status": "active",
                "deprecation_date": None, "eol_date": None,
                "upgrade_target": None},
    # Go (provided.al2 is the custom runtime for Go)
    "provided.al2": {"family": "custom", "version": "AL2", "status": "active",
                     "deprecation_date": "2025-09-01", "eol_date": None,
                     "upgrade_target": "provided.al2023"},
    "provided.al2023": {"family": "custom", "version": "AL2023", "status": "active",
                        "deprecation_date": None, "eol_date": None,
                        "upgrade_target": None},
}


# ---------------------------------------------------------------------------
# Deterministic breaking-change ruleset
#
# These patterns let the Lambda detect known compatibility issues in source
# code WITHOUT invoking the LLM. Detection is a simple per-line substring /
# regex scan — fast and reproducible. The LLM later interprets these findings
# (effort, migration steps) but does not need to read raw code to FIND them.
#
# Each rule: {"pattern": <substring or regex>, "regex": bool, "message": str,
#             "fix": str, "min_target": <runtime version this applies from>}
# Keyed by runtime family.
# ---------------------------------------------------------------------------

# Python stdlib modules removed in 3.12 (PEP 594 + distutils removal)
_PY312_REMOVED_MODULES = [
    "distutils", "imp", "aifc", "audioop", "cgi", "cgitb", "chunk", "crypt",
    "imghdr", "mailcap", "msilib", "nis", "nntplib", "ossaudiodev", "pipes",
    "sndhdr", "spwd", "sunau", "telnetlib", "uu", "xdrlib",
]

BREAKING_CHANGES = {
    "python": {
        # Applies when upgrading TO 3.12+ (from <=3.11)
        "code": [
            {
                "regex": True,
                "pattern": rf"^\s*(?:import|from)\s+({'|'.join(_PY312_REMOVED_MODULES)})\b",
                "message": "Uses a stdlib module removed in Python 3.12",
                "fix": "Replace with a maintained equivalent (e.g. cgi→email.message, distutils→setuptools/packaging).",
                "applies_to_targets": ["python3.12", "python3.13"],
            },
        ],
        "deps": [
            {
                "regex": True,
                "pattern": r"boto3\s*==\s*1\.(?:[0-9]|1[0-9]|2[0-9])\.",
                "message": "Pinned to an older boto3 (<1.30) — runtime bundles a newer version",
                "fix": "Pin boto3>=1.34.0 in requirements.txt to match the target runtime's bundled SDK.",
                "applies_to_targets": ["python3.12", "python3.13"],
            },
        ],
    },
    "nodejs": {
        "code": [
            {
                "regex": True,
                "pattern": r"require\(\s*['\"]aws-sdk['\"]\s*\)",
                "message": "Uses AWS SDK v2 (aws-sdk) — not bundled in nodejs18.x+",
                "fix": "Migrate to modular AWS SDK v3: @aws-sdk/client-<service>.",
                "applies_to_targets": ["nodejs18.x", "nodejs20.x", "nodejs22.x"],
            },
            {
                "regex": True,
                "pattern": r"\bcrypto\.createCipher\b",
                "message": "crypto.createCipher removed/behaves differently under OpenSSL 3.0 (Node 18+)",
                "fix": "Use crypto.createCipheriv with an explicit IV.",
                "applies_to_targets": ["nodejs18.x", "nodejs20.x", "nodejs22.x"],
            },
            {
                "regex": True,
                "pattern": r"\burl\.parse\s*\(",
                "message": "url.parse() is deprecated in Node 18+",
                "fix": "Use the WHATWG URL API (new URL(...)).",
                "applies_to_targets": ["nodejs20.x", "nodejs22.x"],
            },
        ],
        "deps": [
            {
                "regex": True,
                "pattern": r"\"aws-sdk\"\s*:",
                "message": "package.json depends on aws-sdk v2",
                "fix": "Replace with @aws-sdk/client-* v3 modular packages.",
                "applies_to_targets": ["nodejs18.x", "nodejs20.x", "nodejs22.x"],
            },
        ],
    },
    "java": {
        "code": [
            {
                "regex": True,
                "pattern": r"import\s+javax\.(xml\.bind|activation)",
                "message": "Uses javax.xml.bind / javax.activation — removed in Java 11+ (JPMS)",
                "fix": "Add jakarta.xml.bind / jakarta.activation as explicit Maven/Gradle deps.",
                "applies_to_targets": ["java11", "java17", "java21"],
            },
            {
                "regex": True,
                "pattern": r"import\s+com\.amazonaws\.",
                "message": "Uses AWS SDK for Java v1 (com.amazonaws)",
                "fix": "Migrate to AWS SDK v2 (software.amazon.awssdk).",
                "applies_to_targets": ["java11", "java17", "java21"],
            },
        ],
        "deps": [],
    },
    "dotnet": {
        "code": [
            {
                "regex": True,
                "pattern": r"Amazon\.Lambda\.AspNetCoreServer(?!\.Hosting)",
                "message": "Uses Amazon.Lambda.AspNetCoreServer (pre-.NET 8 hosting model)",
                "fix": "Migrate to Amazon.Lambda.AspNetCoreServer.Hosting.",
                "applies_to_targets": ["dotnet8"],
            },
        ],
        "deps": [
            {
                "regex": True,
                "pattern": r"<TargetFramework>net6\.0</TargetFramework>",
                "message": ".csproj targets net6.0 (EOL)",
                "fix": "Update <TargetFramework> to net8.0.",
                "applies_to_targets": ["dotnet8"],
            },
        ],
    },
    "ruby": {"code": [], "deps": []},
    "custom": {"code": [], "deps": []},
}

# Which file extensions are "code" for a given family (used to scope scanning)
_FAMILY_CODE_EXTS = {
    "python": {".py"},
    "nodejs": {".js", ".mjs", ".cjs", ".ts"},
    "java": {".java"},
    "dotnet": {".cs"},
    "ruby": {".rb"},
    "custom": {".go", ".rs"},
}
_DEP_FILENAMES = {
    "requirements.txt", "package.json", "pom.xml", "build.gradle",
    "Gemfile", "go.mod",
}


# ---------------------------------------------------------------------------
# Deterministic per-function enrichment (no extra API calls needed)
# ---------------------------------------------------------------------------

# Name substrings that strongly indicate a throwaway / non-production function.
_TEST_ARTIFACT_TOKENS = (
    "test", "tese", "delete-me", "deleteme", "intern", "hello-world",
    "helloworld", "demo", "poc", "scratch", "tmp", "temp-", "-temp",
    "example", "sample", "playground", "sandbox",
)

# Runtime families that require a real code/SDK migration (RED effort).
# Everything else on a deprecated runtime is a lower-effort version bump.
_SDK_MIGRATION_RUNTIMES = {
    "nodejs14.x", "nodejs16.x",   # aws-sdk v2 → v3
    "java8", "java8.al2",         # AWS SDK v1 → v2, javax removal
    "dotnet6",                    # hosting model + System.Text.Json
}
_NATIVE_REBUILD_RUNTIMES = {
    "provided.al2",               # recompile native binaries for AL2023
}


def _is_test_artifact(function_name: str) -> bool:
    """Heuristic: does the function name look like a throwaway/test artifact?

    Uses token boundaries (start/end of string or a - / _ separator) so we
    match 'test-2', 'my-test', 'delete-me' but NOT 'latest-orders' or
    'contest-winner' where the token is embedded mid-word.
    """
    name = function_name.lower()
    segments = re.split(r"[-_./]", name)
    seg_set = set(segments)
    # Normalize the name with unified separators so multi-word tokens like
    # 'delete-me' / 'hello-world' can be matched with boundaries too.
    norm = re.sub(r"[-_./]", "-", name)
    for tok in _TEST_ARTIFACT_TOKENS:
        tok_norm = re.sub(r"[-_./]", "-", tok)
        if "-" in tok_norm:
            # multi-word token: match as a bounded phrase
            if re.search(rf"(?:^|-){re.escape(tok_norm)}(?:-|$)", norm):
                return True
            continue
        # single-word token: whole-segment match, or token + trailing digits
        if tok_norm in seg_set:
            return True
        for seg in segments:
            if seg.startswith(tok_norm) and seg[len(tok_norm):].isdigit():
                return True
    return False


def _effort_bucket(runtime: str, has_layers: bool = False) -> dict:
    """Classify upgrade effort WITHOUT downloading code — from runtime family.

    Returns {"bucket": "green|yellow|red", "label": str, "reason": str}.
    """
    if runtime in _SDK_MIGRATION_RUNTIMES:
        return {"bucket": "red", "label": "SDK migration / breaking changes",
                "reason": "Runtime requires an AWS SDK major-version migration or stdlib removals."}
    if runtime in _NATIVE_REBUILD_RUNTIMES:
        return {"bucket": "yellow", "label": "Native rebuild",
                "reason": "Custom runtime — native binaries must be recompiled for the new base image."}
    if has_layers:
        return {"bucket": "yellow", "label": "Dependency/layer rebuild",
                "reason": "Attached layers must be verified/rebuilt for the target runtime."}
    return {"bucket": "green", "label": "Direct runtime flip",
            "reason": "Minor version bump — no known breaking changes; flip the runtime setting."}


# Family-level generic upgrade recommendation, used when we can't (or don't)
# download a function's code but still need actionable guidance per function.
_GENERIC_RECOMMENDATION = {
    "python": "Bump the runtime setting; if upgrading to 3.12+, replace any removed stdlib modules (cgi, distutils, imp, telnetlib, etc.) and re-pin boto3>=1.34 in requirements.txt.",
    "nodejs": "Migrate AWS SDK v2 (aws-sdk) to modular v3 (@aws-sdk/client-*); verify OpenSSL-3.0-sensitive crypto; drop deprecated url.parse().",
    "java": "Migrate AWS SDK v1 (com.amazonaws) to v2 (software.amazon.awssdk); add jakarta.xml.bind / jakarta.activation deps (javax.* removed in 11+); bump maven-compiler-plugin target.",
    "dotnet": "Update <TargetFramework> to net8.0; move to Amazon.Lambda.AspNetCoreServer.Hosting; review System.Text.Json breaking changes.",
    "ruby": "Minor upgrade — bundle update and verify gems; few breaking changes 3.2→3.3.",
    "custom": "Rebuild native binaries against AL2023 (glibc 2.34+, OpenSSL 3.0); ensure Go static linking / Rust rebuild.",
    "unknown": "Verify runtime compatibility against the AWS Lambda runtime documentation.",
}


def _generic_recommendation(family: str) -> str:
    return _GENERIC_RECOMMENDATION.get(family, _GENERIC_RECOMMENDATION["unknown"])


def _fetch_last_invoked_days(cw_client, function_names: list, region: str) -> dict:
    """Best-effort: days since last invocation per function, via one batched
    CloudWatch GetMetricData call. Returns {function_name: days_or_None}.

    Bounded and non-fatal: any failure returns an empty map so the report
    still generates. GetMetricData supports up to 500 queries per call.
    """
    from datetime import timedelta
    result: dict = {}
    if not function_names:
        return result
    try:
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=90)
        # Build up to 500 queries (one per function). Cap to stay safe.
        queries = []
        index_map = {}
        for i, fname in enumerate(function_names[:500]):
            qid = f"m{i}"
            index_map[qid] = fname
            queries.append({
                "Id": qid,
                "MetricStat": {
                    "Metric": {
                        "Namespace": "AWS/Lambda",
                        "MetricName": "Invocations",
                        "Dimensions": [{"Name": "FunctionName", "Value": fname}],
                    },
                    "Period": 86400,
                    "Stat": "Sum",
                },
                "ReturnData": True,
            })
        resp = cw_client.get_metric_data(
            MetricDataQueries=queries, StartTime=start, EndTime=end,
            ScanBy="TimestampDescending",
        )
        for r in resp.get("MetricDataResults", []):
            fname = index_map.get(r.get("Id", ""))
            if not fname:
                continue
            timestamps = r.get("Timestamps", [])
            if timestamps:
                # Most recent day with invocations
                most_recent = max(timestamps)
                result[fname] = (end - most_recent).days
            else:
                result[fname] = None  # no invocations in the 90-day window
    except Exception:
        # Non-fatal — return whatever we have (possibly empty)
        return result
    return result


def _analyze_code_for_breaking_changes(source_files: dict, family: str,
                                       target_runtime: Optional[str]) -> list:
    """Deterministically scan downloaded source for known breaking changes.

    Runs entirely in Python (no LLM). Returns a list of findings, each with
    the file, 1-based line number, the offending snippet, a message, and a
    fix hint. This is the data the LLM turns into migration guidance.
    """
    findings: list = []
    rules = BREAKING_CHANGES.get(family, {})
    code_rules = rules.get("code", [])
    dep_rules = rules.get("deps", [])
    code_exts = _FAMILY_CODE_EXTS.get(family, set())

    def _rule_applies(rule) -> bool:
        targets = rule.get("applies_to_targets")
        if not targets or not target_runtime:
            return True
        return target_runtime in targets

    for filename, content in source_files.items():
        if not isinstance(content, str) or content.startswith("[SKIPPED"):
            continue
        ext = os.path.splitext(filename)[1].lower()
        basename = os.path.basename(filename)
        is_code = ext in code_exts
        is_dep = basename in _DEP_FILENAMES

        active_rules = []
        if is_code:
            active_rules = [r for r in code_rules if _rule_applies(r)]
        elif is_dep:
            active_rules = [r for r in dep_rules if _rule_applies(r)]
        if not active_rules:
            continue

        lines = content.splitlines()
        for lineno, line in enumerate(lines, start=1):
            for rule in active_rules:
                matched = False
                if rule.get("regex"):
                    try:
                        if re.search(rule["pattern"], line):
                            matched = True
                    except re.error:
                        matched = False
                else:
                    matched = rule["pattern"] in line
                if matched:
                    findings.append({
                        "file": filename,
                        "line": lineno,
                        "snippet": line.strip()[:200],
                        "message": rule["message"],
                        "fix": rule["fix"],
                        "kind": "dependency" if is_dep else "code",
                    })

    return findings


def _get_lambda_client(region: Optional[str] = None):
    """Get Lambda client, optionally for a specific region."""
    from shared.cross_account import get_aws_client
    return get_aws_client("lambda", region_name=region)


def _classify_runtime(runtime_id: str) -> dict:
    """Classify a runtime's support status."""
    info = RUNTIME_SUPPORT_DATA.get(runtime_id)
    if not info:
        return {"status": "unknown", "runtime": runtime_id}
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    effective_status = info["status"]
    if info.get("eol_date") and now > info["eol_date"]:
        effective_status = "end_of_life"
    elif info.get("deprecation_date") and now > info["deprecation_date"]:
        effective_status = "deprecated"
    return {
        "runtime": runtime_id,
        "family": info["family"],
        "version": info["version"],
        "status": effective_status,
        "deprecation_date": info.get("deprecation_date"),
        "eol_date": info.get("eol_date"),
        "upgrade_target": info.get("upgrade_target"),
    }


def handle_discover_lambda_regions(event):
    """Discover which AWS regions have Lambda functions deployed.

    Scans all enabled regions in parallel to find which ones contain
    Lambda functions. Returns region name, function count, and a breakdown
    of deprecated/EOL functions per region so the user can choose which
    regions to include in a detailed report.
    """
    try:
        from shared.cross_account import get_aws_client
        # Get all enabled regions
        ec2_client = get_aws_client("ec2", region_name="us-east-1")
        regions_response = ec2_client.describe_regions(
            Filters=[{"Name": "opt-in-status", "Values": ["opt-in-not-required", "opted-in"]}]
        )
        all_regions = [r["RegionName"] for r in regions_response.get("Regions", [])]

        def _scan_region(region_name):
            """Count functions in a single region."""
            try:
                client = get_aws_client("lambda", region_name=region_name)
                total = 0
                deprecated_count = 0
                eol_count = 0
                runtimes_found = {}
                paginator = client.get_paginator("list_functions")
                for page in paginator.paginate():
                    for fn in page.get("Functions", []):
                        total += 1
                        runtime = fn.get("Runtime", "")
                        if runtime:
                            runtimes_found[runtime] = runtimes_found.get(runtime, 0) + 1
                            info = _classify_runtime(runtime)
                            if info["status"] == "deprecated":
                                deprecated_count += 1
                            elif info["status"] == "end_of_life":
                                eol_count += 1
                return {
                    "region": region_name,
                    "total_functions": total,
                    "deprecated_count": deprecated_count,
                    "eol_count": eol_count,
                    "needs_attention": deprecated_count + eol_count,
                    "runtimes": runtimes_found,
                }
            except Exception as e:
                return {"region": region_name, "total_functions": 0, "error": str(e)}

        # Scan all regions in parallel (max 10 threads)
        results = []
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(_scan_region, r): r for r in all_regions}
            for future in as_completed(futures):
                result = future.result()
                if result.get("total_functions", 0) > 0:
                    results.append(result)

        # Sort by needs_attention (deprecated + EOL) desc, then total desc
        results.sort(key=lambda r: (-r.get("needs_attention", 0), -r.get("total_functions", 0)))

        total_functions = sum(r["total_functions"] for r in results)
        total_deprecated = sum(r.get("deprecated_count", 0) for r in results)
        total_eol = sum(r.get("eol_count", 0) for r in results)

        return {
            "regions_with_functions": results,
            "total_regions": len(results),
            "total_functions": total_functions,
            "total_deprecated": total_deprecated,
            "total_eol": total_eol,
            "total_needing_attention": total_deprecated + total_eol,
            "note": "Select the regions you want included in the detailed upgrade report.",
        }
    except Exception as e:
        return {"error": str(e)}


def handle_list_functions_by_runtime(event):
    """List Lambda functions, optionally filtered by runtime or region."""
    region = event.get("region")
    runtime_filter = event.get("runtime")
    function_name_filter = event.get("function_name_filter")
    max_results = min(event.get("max_results", 100), 500)

    try:
        client = _get_lambda_client(region)
        functions = []
        paginator = client.get_paginator("list_functions")

        for page in paginator.paginate():
            for fn in page.get("Functions", []):
                runtime = fn.get("Runtime", "")
                if runtime_filter and runtime != runtime_filter:
                    continue
                # Substring match on function name (case-insensitive)
                fn_name = fn["FunctionName"]
                if function_name_filter and function_name_filter.lower() not in fn_name.lower():
                    continue
                runtime_info = _classify_runtime(runtime)
                functions.append({
                    "function_name": fn["FunctionName"],
                    "runtime": runtime,
                    "runtime_status": runtime_info["status"],
                    "upgrade_target": runtime_info.get("upgrade_target"),
                    "handler": fn.get("Handler", ""),
                    "last_modified": fn.get("LastModified", ""),
                    "memory_mb": fn.get("MemorySize", 0),
                    "code_size_bytes": fn.get("CodeSize", 0),
                    "architecture": fn.get("Architectures", ["x86_64"]),
                })
                if len(functions) >= max_results:
                    break
            if len(functions) >= max_results:
                break

        return {
            "functions": functions,
            "count": len(functions),
            "region": region or os.environ.get("AWS_REGION", "us-east-1"),
            "filter_applied": {"runtime": runtime_filter} if runtime_filter else None,
        }
    except Exception as e:
        return {"error": str(e)}


def handle_get_function_configuration(event):
    """Get detailed configuration for a specific Lambda function."""
    function_name = event.get("function_name")
    region = event.get("region")

    if not function_name:
        return {"error": "function_name is required"}

    try:
        client = _get_lambda_client(region)
        config = client.get_function_configuration(FunctionName=function_name)
        runtime = config.get("Runtime", "")
        runtime_info = _classify_runtime(runtime)

        layers = []
        for layer in config.get("Layers", []):
            layers.append({
                "arn": layer.get("Arn", ""),
                "code_size": layer.get("CodeSize", 0),
            })

        return {
            "function_name": config["FunctionName"],
            "function_arn": config["FunctionArn"],
            "runtime": runtime,
            "runtime_status": runtime_info,
            "handler": config.get("Handler", ""),
            "code_size_bytes": config.get("CodeSize", 0),
            "memory_mb": config.get("MemorySize", 128),
            "timeout_seconds": config.get("Timeout", 3),
            "last_modified": config.get("LastModified", ""),
            "architecture": config.get("Architectures", ["x86_64"]),
            "layers": layers,
            "environment_variables": list(
                config.get("Environment", {}).get("Variables", {}).keys()
            ),
            "package_type": config.get("PackageType", "Zip"),
            "ephemeral_storage_mb": config.get(
                "EphemeralStorage", {}
            ).get("Size", 512),
        }
    except Exception as e:
        return {"error": str(e)}


def _download_and_extract_code(function_name: str, region: Optional[str],
                               max_file_size_kb: int = 50,
                               include_patterns: Optional[list] = None) -> dict:
    """Shared helper: download a function's zip and extract source/dep files.

    Returns {"source_files": {...}, "total_size_kb": float, "package_type": str}
    or {"error": ...} / {"package_type": "Image", "source_files": {}} for
    container-image functions (which cannot be downloaded).
    """
    CODE_EXTENSIONS = {
        ".py", ".js", ".ts", ".mjs", ".cjs",
        ".java", ".cs", ".rb", ".go",
        ".json", ".yaml", ".yml", ".toml", ".cfg", ".txt",
    }
    DEP_FILES = {
        "requirements.txt", "package.json", "pom.xml", "build.gradle",
        "Gemfile", "go.mod", "go.sum", ".csproj",
    }

    client = _get_lambda_client(region)
    response = client.get_function(FunctionName=function_name)
    code = response.get("Code", {})

    # Container-image packaging: no downloadable zip
    if code.get("ImageUri") and not code.get("Location"):
        return {"package_type": "Image", "source_files": {}, "total_size_kb": 0.0,
                "note": "Container-image function — source not downloadable."}

    code_location = code.get("Location")
    if not code_location:
        return {"error": "Unable to retrieve function code location"}

    http = urllib3.PoolManager()
    resp = http.request("GET", code_location)
    if resp.status != 200:
        return {"error": f"Failed to download code: HTTP {resp.status}"}

    zip_data = BytesIO(resp.data)
    files: dict = {}
    total_size = 0
    max_total_kb = 200

    with zipfile.ZipFile(zip_data, "r") as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            filename = info.filename
            ext = os.path.splitext(filename)[1].lower()
            basename = os.path.basename(filename)

            skip_dirs = {"node_modules/", "__pycache__/", ".git/",
                         "vendor/", "venv/", ".venv/", "site-packages/"}
            if any(d in filename for d in skip_dirs):
                continue

            is_code = ext in CODE_EXTENSIONS
            is_dep = basename in DEP_FILES
            if not is_code and not is_dep:
                continue

            if include_patterns:
                if not any(p in filename for p in include_patterns):
                    continue

            if info.file_size > max_file_size_kb * 1024:
                files[filename] = f"[SKIPPED: {info.file_size // 1024}KB exceeds limit]"
                continue
            if total_size + info.file_size > max_total_kb * 1024:
                files[filename] = "[SKIPPED: total extraction limit reached]"
                continue

            try:
                content = zf.read(info.filename).decode("utf-8", errors="replace")
                files[filename] = content
                total_size += len(content)
            except Exception:
                files[filename] = "[SKIPPED: binary or unreadable]"

    return {
        "package_type": "Zip",
        "source_files": files,
        "total_size_kb": round(total_size / 1024, 1),
    }


def handle_get_function_code(event):
    """Download and return the function's source code for compatibility analysis.

    Returns the contents of .py, .js, .ts, .java, .cs, .rb, .go files
    from the deployment package (up to a size limit to avoid overwhelming
    the agent context).
    """
    function_name = event.get("function_name")
    region = event.get("region")
    max_file_size_kb = event.get("max_file_size_kb", 50)
    include_patterns = event.get("include_patterns")

    if not function_name:
        return {"error": "function_name is required"}

    try:
        result = _download_and_extract_code(
            function_name, region, max_file_size_kb, include_patterns
        )
        if "error" in result:
            return result
        files = result.get("source_files", {})
        return {
            "function_name": function_name,
            "package_type": result.get("package_type", "Zip"),
            "files_extracted": len(files),
            "total_size_kb": result.get("total_size_kb", 0.0),
            "source_files": files,
            "note": result.get("note", ""),
        }
    except Exception as e:
        return {"error": str(e)}


def handle_get_runtime_support_status(event):
    """Return all Lambda runtimes with their support/deprecation status."""
    family_filter = event.get("family")

    runtimes = []
    for runtime_id, info in RUNTIME_SUPPORT_DATA.items():
        if family_filter and info["family"] != family_filter:
            continue
        classified = _classify_runtime(runtime_id)
        runtimes.append(classified)

    # Sort: deprecated/EOL first, then by family
    status_order = {"end_of_life": 0, "deprecated": 1, "active": 2, "unknown": 3}
    runtimes.sort(key=lambda r: (status_order.get(r["status"], 3), r["family"], r["runtime"]))

    summary = {
        "active": sum(1 for r in runtimes if r["status"] == "active"),
        "deprecated": sum(1 for r in runtimes if r["status"] == "deprecated"),
        "end_of_life": sum(1 for r in runtimes if r["status"] == "end_of_life"),
    }

    return {
        "runtimes": runtimes,
        "total": len(runtimes),
        "summary": summary,
        "note": "Dates are approximate and based on AWS published schedules. "
                "Check AWS documentation for latest updates.",
    }


def handle_get_deprecated_functions(event):
    """Find all functions using deprecated or end-of-life runtimes."""
    region = event.get("region")
    include_approaching = event.get("include_approaching_deprecation", False)
    max_results = min(event.get("max_results", 200), 500)

    try:
        client = _get_lambda_client(region)
        deprecated_functions = []
        paginator = client.get_paginator("list_functions")

        for page in paginator.paginate():
            for fn in page.get("Functions", []):
                runtime = fn.get("Runtime", "")
                if not runtime:
                    continue
                runtime_info = _classify_runtime(runtime)
                status = runtime_info.get("status", "unknown")

                include = status in ("deprecated", "end_of_life")
                if include_approaching and status == "active":
                    dep_date = runtime_info.get("deprecation_date")
                    if dep_date:
                        now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                        # Include if deprecation is within 6 months
                        from datetime import timedelta
                        threshold = (
                            datetime.now(timezone.utc) + timedelta(days=180)
                        ).strftime("%Y-%m-%d")
                        if dep_date <= threshold:
                            include = True

                if include:
                    deprecated_functions.append({
                        "function_name": fn["FunctionName"],
                        "runtime": runtime,
                        "runtime_status": status,
                        "deprecation_date": runtime_info.get("deprecation_date"),
                        "eol_date": runtime_info.get("eol_date"),
                        "upgrade_target": runtime_info.get("upgrade_target"),
                        "last_modified": fn.get("LastModified", ""),
                        "code_size_bytes": fn.get("CodeSize", 0),
                    })
                    if len(deprecated_functions) >= max_results:
                        break
            if len(deprecated_functions) >= max_results:
                break

        # Group by runtime for summary
        by_runtime = {}
        for fn in deprecated_functions:
            rt = fn["runtime"]
            if rt not in by_runtime:
                by_runtime[rt] = {"count": 0, "status": fn["runtime_status"],
                                  "upgrade_target": fn["upgrade_target"]}
            by_runtime[rt]["count"] += 1

        return {
            "deprecated_functions": deprecated_functions,
            "count": len(deprecated_functions),
            "by_runtime": by_runtime,
            "region": region or os.environ.get("AWS_REGION", "us-east-1"),
            "include_approaching_deprecation": include_approaching,
        }
    except Exception as e:
        return {"error": str(e)}


def handle_get_deprecated_functions_multi_region(event):
    """Find deprecated/EOL functions across multiple regions in parallel.

    This is the fast-path for report generation: scans all specified regions
    concurrently instead of requiring sequential per-region calls.
    """
    regions = event.get("regions", [])
    include_approaching = event.get("include_approaching_deprecation", False)
    max_results_per_region = min(event.get("max_results_per_region", 100), 500)

    if not regions:
        return {"error": "regions array is required (e.g., ['us-east-1', 'eu-west-1'])"}

    def _scan_region(region):
        """Scan a single region for deprecated functions."""
        try:
            client = _get_lambda_client(region)
            deprecated_functions = []
            paginator = client.get_paginator("list_functions")

            for page in paginator.paginate():
                for fn in page.get("Functions", []):
                    runtime = fn.get("Runtime", "")
                    if not runtime:
                        continue
                    runtime_info = _classify_runtime(runtime)
                    status = runtime_info.get("status", "unknown")

                    include = status in ("deprecated", "end_of_life")
                    if include_approaching and status == "active":
                        dep_date = runtime_info.get("deprecation_date")
                        if dep_date:
                            from datetime import timedelta
                            threshold = (
                                datetime.now(timezone.utc) + timedelta(days=180)
                            ).strftime("%Y-%m-%d")
                            if dep_date <= threshold:
                                include = True

                    if include:
                        deprecated_functions.append({
                            "function_name": fn["FunctionName"],
                            "runtime": runtime,
                            "runtime_status": status,
                            "deprecation_date": runtime_info.get("deprecation_date"),
                            "eol_date": runtime_info.get("eol_date"),
                            "upgrade_target": runtime_info.get("upgrade_target"),
                            "last_modified": fn.get("LastModified", ""),
                            "code_size_bytes": fn.get("CodeSize", 0),
                            "handler": fn.get("Handler", ""),
                            "memory_mb": fn.get("MemorySize", 0),
                        })
                        if len(deprecated_functions) >= max_results_per_region:
                            break
                if len(deprecated_functions) >= max_results_per_region:
                    break

            return {"region": region, "functions": deprecated_functions, "count": len(deprecated_functions)}
        except Exception as e:
            return {"region": region, "functions": [], "count": 0, "error": str(e)}

    # Scan all requested regions in parallel
    all_results = []
    with ThreadPoolExecutor(max_workers=min(len(regions), 10)) as executor:
        futures = {executor.submit(_scan_region, r): r for r in regions}
        for future in as_completed(futures):
            all_results.append(future.result())

    # Sort results by region name for consistent output
    all_results.sort(key=lambda r: r["region"])

    # Build summary
    total_functions = sum(r["count"] for r in all_results)
    by_runtime = {}
    by_priority = {"end_of_life": 0, "deprecated": 0, "approaching": 0}
    for region_result in all_results:
        for fn in region_result["functions"]:
            rt = fn["runtime"]
            if rt not in by_runtime:
                by_runtime[rt] = {"count": 0, "status": fn["runtime_status"],
                                  "upgrade_target": fn["upgrade_target"]}
            by_runtime[rt]["count"] += 1
            if fn["runtime_status"] == "end_of_life":
                by_priority["end_of_life"] += 1
            elif fn["runtime_status"] == "deprecated":
                by_priority["deprecated"] += 1
            else:
                by_priority["approaching"] += 1

    return {
        "results_by_region": all_results,
        "total_functions": total_functions,
        "regions_scanned": len(regions),
        "by_runtime": by_runtime,
        "by_priority": by_priority,
        "include_approaching_deprecation": include_approaching,
    }


# ---------------------------------------------------------------------------
# Deterministic report renderer — builds the full report markdown in Python
# so the LLM never has to (guarantees code recommendations appear + is fast).
# ---------------------------------------------------------------------------

_EFFORT_EMOJI = {"green": "🟢", "yellow": "🟡", "red": "🔴"}


def _render_report_markdown(summary: dict, critical_by_region: dict,
                            deletion_candidates: list, grouping: dict,
                            high_medium: dict) -> str:
    """Render the complete upgrade report as markdown, deterministically.

    This is the 'collector' work moved into Python: every critical function is
    listed with its code/library recommendation without relying on the LLM to
    format a large JSON payload. The agent outputs this verbatim.
    """
    bp = summary.get("by_priority", {})
    be = summary.get("by_effort", {})
    lines: list = []

    lines.append("## Executive Summary")
    lines.append("")
    lines.append("_Source: LIVE data from your AWS account (Lambda ListFunctions / "
                 "GetFunction + CloudWatch). This is not demo or mock data._")
    lines.append("")
    lines.append(f"- **Total functions needing upgrade:** {summary.get('total_deprecated_functions', 0)}")
    lines.append(f"- **Priority:** 🔴 CRITICAL (EOL) {bp.get('end_of_life', 0)} | "
                 f"🟠 HIGH (deprecated) {bp.get('deprecated', 0)} | "
                 f"🟡 MEDIUM (approaching) {bp.get('approaching', 0)}")
    lines.append(f"- **Effort:** 🟢 Direct flip {be.get('green', 0)} | "
                 f"🟡 Dependency/native rebuild {be.get('yellow', 0)} | "
                 f"🔴 SDK migration {be.get('red', 0)}")
    lines.append(f"- **Scope:** {summary.get('regions_scanned', 0)} regions scanned, "
                 f"{summary.get('regions_with_issues', 0)} with issues. "
                 f"Code scanned for {summary.get('critical_code_scanned', 0)} of "
                 f"{summary.get('critical_total', 0)} critical functions.")
    lines.append("")

    # Deletion candidates
    lines.append("## Candidates for Deletion (triage first)")
    lines.append("")
    if deletion_candidates:
        lines.append(f"{len(deletion_candidates)} functions look like test artifacts or "
                     f"have not been invoked in 90 days — consider deleting instead of upgrading.")
        lines.append("")
        lines.append("| Function | Region | Runtime | Reason | Last Invoked |")
        lines.append("|----------|--------|---------|--------|--------------|")
        for d in deletion_candidates:
            li = d.get("last_invoked_days")
            li_txt = "never (90d+)" if li is None else f"{li}d ago"
            lines.append(f"| {d['function_name']} | {d['region']} | {d['runtime']} | "
                         f"{d['reason']} | {li_txt} |")
    else:
        lines.append("None detected.")
    lines.append("")

    # Critical functions — full list by region WITH recommendations
    lines.append("## 🔴 CRITICAL Functions — Full List by Region (upgrade immediately)")
    lines.append("")
    if not critical_by_region:
        lines.append("No functions on end-of-life runtimes. 🎉")
        lines.append("")
    for region in sorted(critical_by_region.keys()):
        entries = critical_by_region[region]
        lines.append(f"### Region: {region} ({len(entries)} critical)")
        lines.append("")
        lines.append("| Function | Runtime → Target | Effort | Last Invoked | Recommendation |")
        lines.append("|----------|------------------|--------|--------------|----------------|")
        for e in entries:
            emoji = _EFFORT_EMOJI.get(e.get("effort_bucket", ""), "")
            li = e.get("last_invoked_days")
            li_txt = "never (90d+)" if li is None else f"{li}d ago"
            rec = e.get("recommendation", "").replace("\n", " ").replace("|", "\\|")
            if len(rec) > 300:
                rec = rec[:297] + "..."
            lines.append(f"| {e['function_name']} | {e['runtime']} → {e.get('upgrade_target','?')} | "
                         f"{emoji} {e.get('effort_label','')} | {li_txt} | {rec} |")
        lines.append("")
        # Explicit code-change detail for functions that have findings
        detail = [e for e in entries if e.get("findings")]
        if detail:
            lines.append("**Code changes required in this region:**")
            lines.append("")
            for e in detail:
                lines.append(f"- **{e['function_name']}** ({e['runtime']} → {e.get('upgrade_target','?')}):")
                for f in e["findings"][:8]:
                    kind = "dependency" if f.get("kind") == "dependency" else "code"
                    lines.append(f"    - `{f['file']}:{f['line']}` ({kind}) — {f['message']} "
                                 f"→ **{f['fix']}**")
            lines.append("")

    # HIGH / MEDIUM — summarized, not enumerated
    lines.append("## 🟠 HIGH & 🟡 MEDIUM (summary — batch by effort)")
    lines.append("")
    lines.append(f"- **HIGH (deprecated):** {high_medium.get('high_count', 0)} functions")
    lines.append(f"- **MEDIUM (approaching):** {high_medium.get('medium_count', 0)} functions")
    lines.append("")

    def _grp(title, names):
        if not names:
            return
        shown = names[:25]
        more = f" _(+{len(names) - len(shown)} more)_" if len(names) > len(shown) else ""
        lines.append(f"- **{title}** ({len(names)}): {', '.join(shown)}{more}")

    _grp("🟢 Quick wins — direct runtime flip (batch-upgradable)", grouping.get("quick_wins_green", []))
    _grp("🟡 Dependency / native rebuilds", grouping.get("dependency_rebuilds_yellow", []))
    _grp("🔴 SDK migration required (real refactor)", grouping.get("sdk_migrations_red", []))
    lines.append("")

    # Suggested follow-up prompts — deterministic, tailored to what we found.
    lines.append("## Ask Me Next")
    lines.append("")
    lines.append("Copy any of these as your next prompt:")
    lines.append("")
    # Pick a couple of real function names to make the examples concrete.
    example_fn = None
    example_region = None
    for region in sorted(critical_by_region.keys()):
        if critical_by_region[region]:
            example_region = region
            example_fn = critical_by_region[region][0]["function_name"]
            break
    if example_fn:
        lines.append(f'- "Show me the full migration steps for `{example_fn}`."')
    lines.append('- "Scan only us-east-1 and eu-west-1 for deprecated Lambda functions."')
    lines.append('- "List every 🔴 SDK-migration function and the exact code changes needed."')
    lines.append('- "Which functions are safe to upgrade with a direct runtime flip (no code changes)?"')
    lines.append('- "Show the deletion candidates that have not been invoked in 90 days."')
    if example_region:
        lines.append(f'- "Give me an upgrade plan for just the {example_region} region."')
    lines.append('- "How do I migrate a Node.js function from AWS SDK v2 to v3?"')
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# ONE-SHOT AGGREGATOR — the primary fast path for report generation
# ---------------------------------------------------------------------------


def handle_generate_upgrade_analysis(event):
    """Complete upgrade analysis in a SINGLE tool call — the fast path.

    Performs ALL deterministic work server-side in Python (parallel API calls
    + regex-based code scanning), returning a complete, pre-analyzed dataset.
    The agent only needs AI reasoning for the narrative/playbook on top.

    Steps (all parallel where possible):
    1. Discover or accept regions
    2. Scan all selected regions concurrently for deprecated/EOL functions
    3. Deep-analyze code for ALL CRITICAL (end_of_life) functions (bounded cap),
       plus optionally the top-N HIGH functions
    4. Fetch config + code for those in parallel
    5. Run deterministic breaking-change detection on the code
    6. Return the FULL set of critical functions (grouped-friendly), each with
       a specific or family-generic recommendation; summarize HIGH/MEDIUM
    """
    regions = event.get("regions")  # optional: if None, auto-discover
    # How many extra HIGH-tier functions to also deep-analyze (beyond all criticals)
    max_functions_to_analyze = min(event.get("max_functions_to_analyze", 5), 20)
    # Safety cap on how many critical functions get code downloaded (protects the
    # Lambda timeout). Criticals beyond this get family-generic recommendations.
    max_critical_to_analyze = min(event.get("max_critical_to_analyze", 75), 150)
    include_approaching = event.get("include_approaching_deprecation", True)

    from shared.cross_account import get_aws_client
    errors: list = []

    # --- Step 1: Determine regions ---
    if not regions:
        try:
            ec2_client = get_aws_client("ec2", region_name="us-east-1")
            resp = ec2_client.describe_regions(
                Filters=[{"Name": "opt-in-status", "Values": ["opt-in-not-required", "opted-in"]}]
            )
            all_regions = [r["RegionName"] for r in resp.get("Regions", [])]
        except Exception as e:
            return {"error": f"Failed to discover regions: {e}"}
    else:
        all_regions = regions

    # --- Step 2: Scan regions in parallel for deprecated functions ---
    from datetime import timedelta

    def _scan_region_deprecated(region):
        try:
            client = get_aws_client("lambda", region_name=region)
            functions = []
            paginator = client.get_paginator("list_functions")
            for page in paginator.paginate():
                for fn in page.get("Functions", []):
                    runtime = fn.get("Runtime", "")
                    if not runtime:
                        continue
                    info = _classify_runtime(runtime)
                    status = info.get("status", "unknown")
                    include = status in ("deprecated", "end_of_life")
                    if include_approaching and status == "active":
                        dep_date = info.get("deprecation_date")
                        if dep_date:
                            threshold = (datetime.now(timezone.utc) + timedelta(days=180)).strftime("%Y-%m-%d")
                            if dep_date <= threshold:
                                include = True
                    if include:
                        functions.append({
                            "function_name": fn["FunctionName"],
                            "runtime": runtime,
                            "runtime_status": status,
                            "family": info.get("family", "unknown"),
                            "deprecation_date": info.get("deprecation_date"),
                            "eol_date": info.get("eol_date"),
                            "upgrade_target": info.get("upgrade_target"),
                            "handler": fn.get("Handler", ""),
                            "last_modified": fn.get("LastModified", ""),
                            "code_size_bytes": fn.get("CodeSize", 0),
                            "memory_mb": fn.get("MemorySize", 0),
                            "package_type": fn.get("PackageType", "Zip"),
                            "region": region,
                        })
            return functions
        except Exception as e:
            errors.append(f"Region {region}: {e}")
            return []

    all_deprecated = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(_scan_region_deprecated, r): r for r in all_regions}
        for future in as_completed(futures):
            all_deprecated.extend(future.result())

    # --- Enrich each function deterministically (no extra API calls) ---
    for fn in all_deprecated:
        fn["is_test_artifact"] = _is_test_artifact(fn["function_name"])
        eb = _effort_bucket(fn["runtime"], has_layers=False)
        fn["effort_bucket"] = eb["bucket"]
        fn["effort_label"] = eb["label"]
        fn["effort_reason"] = eb["reason"]

    # --- Best-effort: last-invoked days via batched CloudWatch (per region) ---
    include_last_invoked = event.get("include_last_invoked", True)
    if include_last_invoked:
        by_region_names: dict = {}
        for fn in all_deprecated:
            by_region_names.setdefault(fn["region"], []).append(fn["function_name"])

        def _region_last_invoked(region_names):
            region, names = region_names
            try:
                cw = get_aws_client("cloudwatch", region_name=region)
                return region, _fetch_last_invoked_days(cw, names, region)
            except Exception:
                return region, {}

        li_map: dict = {}
        with ThreadPoolExecutor(max_workers=min(len(by_region_names) or 1, 10)) as executor:
            for region, m in executor.map(_region_last_invoked, by_region_names.items()):
                li_map[region] = m
        for fn in all_deprecated:
            fn["last_invoked_days"] = li_map.get(fn["region"], {}).get(fn["function_name"])

    # --- Sort by priority: EOL first, then deprecated, then approaching ---
    priority_order = {"end_of_life": 0, "deprecated": 1, "active": 2}
    all_deprecated.sort(key=lambda f: (
        priority_order.get(f["runtime_status"], 3),
        f.get("last_modified", ""),  # oldest first within same priority
    ))

    # --- Build summary stats ---
    by_region: dict = {}
    by_runtime: dict = {}
    by_priority = {"end_of_life": 0, "deprecated": 0, "approaching": 0}
    by_effort = {"green": 0, "yellow": 0, "red": 0}
    for fn in all_deprecated:
        r = fn["region"]
        by_region.setdefault(r, {"count": 0, "eol": 0, "deprecated": 0, "approaching": 0})
        by_region[r]["count"] += 1
        if fn["runtime_status"] == "end_of_life":
            by_region[r]["eol"] += 1
            by_priority["end_of_life"] += 1
        elif fn["runtime_status"] == "deprecated":
            by_region[r]["deprecated"] += 1
            by_priority["deprecated"] += 1
        else:
            by_region[r]["approaching"] += 1
            by_priority["approaching"] += 1

        by_effort[fn["effort_bucket"]] = by_effort.get(fn["effort_bucket"], 0) + 1

        rt = fn["runtime"]
        if rt not in by_runtime:
            by_runtime[rt] = {"count": 0, "status": fn["runtime_status"],
                              "family": fn["family"], "upgrade_target": fn["upgrade_target"]}
        by_runtime[rt]["count"] += 1

    # --- Deletion candidates: test artifacts + never/long-idle (>=90d) ---
    deletion_candidates = [
        {
            "function_name": fn["function_name"],
            "region": fn["region"],
            "runtime": fn["runtime"],
            "reason": ("test/throwaway name" if fn["is_test_artifact"] else "no invocations in 90 days"),
            "last_invoked_days": fn.get("last_invoked_days"),
            "last_modified": fn.get("last_modified", ""),
        }
        for fn in all_deprecated
        if fn["is_test_artifact"] or fn.get("last_invoked_days") is None
    ]

    # --- Quick-win buckets (deterministic grouping the report can use) ---
    quick_wins = [f["function_name"] for f in all_deprecated if f["effort_bucket"] == "green"]
    dependency_rebuilds = [f["function_name"] for f in all_deprecated if f["effort_bucket"] == "yellow"]
    sdk_migrations = [f["function_name"] for f in all_deprecated if f["effort_bucket"] == "red"]

    # --- Step 3: Select functions for CODE download + analysis ---
    # Cover ALL critical (end_of_life) functions so every one gets a real
    # code/library recommendation. Then add the top-N HIGH functions.
    critical_all = [f for f in all_deprecated if f["runtime_status"] == "end_of_life"]
    # Order criticals: real (non-test) first, then oldest — so if we hit the cap,
    # the most important ones are the ones we downloaded code for.
    critical_all.sort(key=lambda f: (
        1 if f["is_test_artifact"] else 0,
        f.get("last_modified", ""),
    ))
    critical_to_download = critical_all[:max_critical_to_analyze]

    high_candidates = [
        f for f in all_deprecated
        if f["runtime_status"] == "deprecated" and not f["is_test_artifact"]
    ]
    high_candidates.sort(key=lambda f: (
        0 if f["effort_bucket"] == "red" else 1,
        f.get("last_modified", ""),
    ))
    high_to_download = high_candidates[:max_functions_to_analyze]

    top_functions = critical_to_download + high_to_download

    # --- Step 4+5: Fetch code + analyze in parallel ---
    def _analyze_one_function(fn_info):
        """Fetch config + code + run breaking-change detection for one function."""
        fname = fn_info["function_name"]
        region = fn_info["region"]
        family = fn_info["family"]
        target = fn_info.get("upgrade_target")
        result = {
            "function_name": fname,
            "region": region,
            "runtime": fn_info["runtime"],
            "runtime_status": fn_info["runtime_status"],
            "upgrade_target": target,
            "family": family,
            "handler": fn_info.get("handler", ""),
            "code_size_bytes": fn_info.get("code_size_bytes", 0),
            "memory_mb": fn_info.get("memory_mb", 0),
            "last_modified": fn_info.get("last_modified", ""),
        }

        # Get config (layers, architecture)
        try:
            client = _get_lambda_client(region)
            config = client.get_function_configuration(FunctionName=fname)
            result["layers"] = [l.get("Arn", "") for l in config.get("Layers", [])]
            result["architecture"] = config.get("Architectures", ["x86_64"])
            result["timeout_seconds"] = config.get("Timeout", 3)
            result["env_var_keys"] = list(config.get("Environment", {}).get("Variables", {}).keys())
        except Exception as e:
            result["config_error"] = str(e)
            result["layers"] = []
            result["architecture"] = []

        # Download + analyze code
        if fn_info.get("package_type", "Zip") == "Image":
            result["code_analysis"] = {
                "status": "skipped",
                "reason": "Container-image function — code not downloadable",
                "findings": [],
            }
        else:
            try:
                code_result = _download_and_extract_code(fname, region)
                if "error" in code_result:
                    result["code_analysis"] = {
                        "status": "error",
                        "reason": code_result["error"],
                        "findings": [],
                    }
                else:
                    source_files = code_result.get("source_files", {})
                    findings = _analyze_code_for_breaking_changes(source_files, family, target)
                    result["code_analysis"] = {
                        "status": "complete",
                        "files_scanned": len(source_files),
                        "total_size_kb": code_result.get("total_size_kb", 0.0),
                        "findings": findings,
                        "breaking_changes_detected": len(findings),
                    }
            except Exception as e:
                result["code_analysis"] = {
                    "status": "error",
                    "reason": str(e),
                    "findings": [],
                }

        return result

    analyzed_functions = []
    if top_functions:
        with ThreadPoolExecutor(max_workers=min(len(top_functions), 15)) as executor:
            futures = [executor.submit(_analyze_one_function, fn) for fn in top_functions]
            for future in as_completed(futures):
                analyzed_functions.append(future.result())

    # Re-sort analyzed by priority
    analyzed_functions.sort(key=lambda f: (
        priority_order.get(f["runtime_status"], 3),
        f["function_name"],
    ))
    # Index analyzed results by (name, region) so we can attach findings to the
    # full critical list below.
    analyzed_index = {(a["function_name"], a["region"]): a for a in analyzed_functions}

    # --- Build the FULL critical list (every EOL function), grouped by region,
    #     each with a specific (scanned) or family-generic recommendation. ---
    critical_by_region: dict = {}
    for fn in critical_all:
        analyzed = analyzed_index.get((fn["function_name"], fn["region"]))
        findings = []
        code_status = "not_scanned"
        if analyzed:
            ca = analyzed.get("code_analysis", {})
            findings = ca.get("findings", [])
            code_status = ca.get("status", "not_scanned")

        if findings:
            recommendation = "; ".join(
                f"{f['file']}:{f['line']} — {f['message']} → {f['fix']}"
                for f in findings[:8]
            )
        elif code_status == "complete":
            recommendation = "No breaking changes detected in scanned code — safe to flip the runtime setting directly."
        else:
            # container image, error, or beyond the download cap → family guidance
            recommendation = _generic_recommendation(fn["family"])

        entry = {
            "function_name": fn["function_name"],
            "runtime": fn["runtime"],
            "upgrade_target": fn.get("upgrade_target"),
            "effort_bucket": fn["effort_bucket"],
            "effort_label": fn["effort_label"],
            "is_test_artifact": fn["is_test_artifact"],
            "last_invoked_days": fn.get("last_invoked_days"),
            "last_modified": fn.get("last_modified", ""),
            "code_size_bytes": fn.get("code_size_bytes", 0),
            "findings": findings,
            "code_scan_status": code_status,
            "recommendation": recommendation,
        }
        critical_by_region.setdefault(fn["region"], []).append(entry)

    critical_analyzed_count = sum(
        1 for fn in critical_all
        if (fn["function_name"], fn["region"]) in analyzed_index
    )

    summary = {
        "total_deprecated_functions": len(all_deprecated),
        "regions_scanned": len(all_regions),
        "regions_with_issues": len(by_region),
        "by_priority": by_priority,
        "by_effort": by_effort,
        "by_region": by_region,
        "by_runtime": by_runtime,
        "critical_total": len(critical_all),
        "critical_code_scanned": critical_analyzed_count,
        "high_analyzed": len(high_to_download),
        "deletion_candidate_count": len(deletion_candidates),
        "quick_win_count": len(quick_wins),
        "dependency_rebuild_count": len(dependency_rebuilds),
        "sdk_migration_count": len(sdk_migrations),
    }
    grouping = {
        "quick_wins_green": quick_wins[:80],
        "dependency_rebuilds_yellow": dependency_rebuilds[:80],
        "sdk_migrations_red": sdk_migrations[:80],
    }
    high_medium_summary = {
        "high_count": by_priority["deprecated"],
        "medium_count": by_priority["approaching"],
        "note": "HIGH/MEDIUM functions are summarized by counts + effort groups; only CRITICAL functions are enumerated in full.",
    }
    deletion_top = deletion_candidates[:60]

    # Pre-render the full report as markdown IN PYTHON. The agent outputs this
    # verbatim — guarantees every critical function + its code recommendation
    # appears (no reliance on the LLM to reformat a large JSON payload), and
    # keeps report generation fast because the LLM writes almost nothing.
    report_markdown = _render_report_markdown(
        summary, critical_by_region, deletion_top, grouping, high_medium_summary
    )

    return {
        "data_source": "live",
        "report_markdown": report_markdown,
        "summary": summary,
        "critical_functions_by_region": critical_by_region,
        "deletion_candidates": deletion_top,
        "grouping": grouping,
        "high_medium_summary": high_medium_summary,
        "errors": errors if errors else None,
        "note": (
            "This data is LIVE from the caller's AWS account (real Lambda "
            "functions via ListFunctions/GetFunction + CloudWatch). It is NOT "
            "demo/mock/sample data — there is no mockScenario here. Do NOT add a "
            "demo-data disclaimer. Function names that look like 'test'/'demo'/"
            "'sample' are REAL function names in the account, flagged as deletion "
            "candidates. PREFERRED: output the 'report_markdown' field VERBATIM as "
            "the body of the report (it already contains the full critical list "
            "with code recommendations), then append only a short 'Migration "
            "Playbook' and 'Rollback Plan'."
        ),
    }
