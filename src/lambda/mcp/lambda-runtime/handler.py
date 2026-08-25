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
    3. Pick top N CRITICAL/HIGH functions
    4. Fetch config + code for those in parallel
    5. Run deterministic breaking-change detection on the code
    6. Return complete structured payload
    """
    regions = event.get("regions")  # optional: if None, auto-discover
    max_functions_to_analyze = min(event.get("max_functions_to_analyze", 5), 10)
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

        rt = fn["runtime"]
        if rt not in by_runtime:
            by_runtime[rt] = {"count": 0, "status": fn["runtime_status"],
                              "family": fn["family"], "upgrade_target": fn["upgrade_target"]}
        by_runtime[rt]["count"] += 1

    # --- Step 3: Pick top N for deep analysis ---
    top_functions = all_deprecated[:max_functions_to_analyze]

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
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(_analyze_one_function, fn) for fn in top_functions]
        for future in as_completed(futures):
            analyzed_functions.append(future.result())

    # Re-sort analyzed by priority
    analyzed_functions.sort(key=lambda f: (
        priority_order.get(f["runtime_status"], 3),
        f["function_name"],
    ))

    # --- Build the final payload ---
    return {
        "summary": {
            "total_deprecated_functions": len(all_deprecated),
            "regions_scanned": len(all_regions),
            "regions_with_issues": len(by_region),
            "by_priority": by_priority,
            "by_region": by_region,
            "by_runtime": by_runtime,
            "functions_analyzed_in_depth": len(analyzed_functions),
        },
        "all_deprecated_functions": [
            {
                "function_name": fn["function_name"],
                "region": fn["region"],
                "runtime": fn["runtime"],
                "runtime_status": fn["runtime_status"],
                "upgrade_target": fn.get("upgrade_target"),
                "last_modified": fn.get("last_modified", ""),
                "code_size_bytes": fn.get("code_size_bytes", 0),
            }
            for fn in all_deprecated
        ],
        "detailed_analysis": analyzed_functions,
        "errors": errors if errors else None,
        "note": (
            "The 'detailed_analysis' section contains code-level findings "
            "(breaking changes detected deterministically). Use these findings "
            "to write migration guidance, effort estimates, and a prioritized "
            "upgrade playbook. Functions NOT in 'detailed_analysis' should "
            "receive generic guidance based on their runtime family."
        ),
    }
