"""
AWS Cost Optimization Hub MCP Server - Lambda Implementation for AgentCore Gateway

Provides tools to query Cost Optimization Hub recommendations and savings summaries.
COH is a us-east-1 only API — always uses us-east-1 regardless of deployment region.

Important: COH must be opted-in via the AWS Console before use. The
`get_enrollment_status` tool checks this.

Tools (4):
- get_enrollment_status: Check if COH is enabled
- list_recommendations: List optimization recommendations with filtering
- get_recommendation: Get details for a specific recommendation
- list_recommendation_summaries: Aggregated savings by dimension

Required IAM Permissions:
- cost-optimization-hub:ListEnrollmentStatuses
- cost-optimization-hub:ListRecommendations
- cost-optimization-hub:GetRecommendation
- cost-optimization-hub:ListRecommendationSummaries
"""

import json

from shared.cross_account import get_aws_client

# Cost Optimization Hub is a us-east-1 only API regardless of deployment region.
COH_REGION = "us-east-1"


def _get_client():
    # role_alias="COH" lets a deploy point COH at a delegated-admin
    # account (set CROSS_ACCOUNT_ROLE_ARN_COH) independently of the
    # default cross-account role. Unset = use the execution role.
    return get_aws_client(
        "cost-optimization-hub", region_name=COH_REGION, role_alias="COH"
    )


def handler(event, context):
    print(f"Event: {json.dumps(event)}")
    extended_tool_name = context.client_context.custom["bedrockAgentCoreToolName"]
    tool_name = extended_tool_name.split("___")[1]
    print(f"Tool name: {tool_name}")

    handlers = {
        "get_enrollment_status": handle_get_enrollment_status,
        "list_recommendations": handle_list_recommendations,
        "get_recommendation": handle_get_recommendation,
        "list_recommendation_summaries": handle_list_recommendation_summaries,
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


def handle_get_enrollment_status(event):
    """Check if Cost Optimization Hub is enabled."""
    try:
        client = _get_client()
        resp = client.list_enrollment_statuses(includeOrganizationInfo=True)
        items = resp.get("items", [])
        if not items:
            return {
                "enrolled": False,
                "status": "NOT_ENROLLED",
                "message": "Cost Optimization Hub is not enabled. Enable it in the AWS Billing console.",
            }
        primary = items[0]
        status = primary.get("status", "Inactive")
        return {
            "enrolled": status == "Active",
            "status": status,
            "account_id": primary.get("accountId", ""),
            "include_member_accounts": resp.get("includeMemberAccounts", False),
        }
    except Exception as e:
        if "AccessDenied" in str(e):
            return {
                "enrolled": False,
                "status": "ACCESS_DENIED",
                "message": "COH not enabled or insufficient permissions.",
            }
        return {"error": str(e)}


def handle_list_recommendations(event):
    """List optimization recommendations with optional filtering."""
    try:
        client = _get_client()
        params = {"maxResults": min(event.get("max_results", 50), 100)}

        filter_obj = {}
        for key, api_key in [
            ("resource_types", "resourceTypes"),
            ("action_types", "actionTypes"),
            ("regions", "regions"),
            ("account_ids", "accountIds"),
            ("implementation_efforts", "implementationEfforts"),
        ]:
            val = event.get(key)
            if val:
                filter_obj[api_key] = [val] if isinstance(val, str) else val
        if filter_obj:
            params["filter"] = filter_obj
        if event.get("include_all_recommendations"):
            params["includeAllRecommendations"] = True
        if event.get("next_token"):
            params["nextToken"] = event["next_token"]

        resp = client.list_recommendations(**params)
        items = resp.get("items", [])
        recs = [
            {
                "recommendation_id": i.get("recommendationId", ""),
                "account_id": i.get("accountId", ""),
                "region": i.get("region", ""),
                "resource_id": i.get("resourceId", ""),
                "current_resource_type": i.get("currentResourceType", ""),
                "recommended_resource_type": i.get("recommendedResourceType", ""),
                "estimated_monthly_savings": i.get("estimatedMonthlySavings", 0),
                "estimated_savings_percentage": i.get("estimatedSavingsPercentage", 0),
                "action_type": i.get("actionType", ""),
                "implementation_effort": i.get("implementationEffort", ""),
            }
            for i in items
        ]
        result = {
            "recommendations": recs,
            "count": len(recs),
            "total_estimated_monthly_savings": round(
                sum(i.get("estimatedMonthlySavings", 0) or 0 for i in items), 2
            ),
        }
        if resp.get("nextToken"):
            result["next_token"] = resp["nextToken"]
        return result
    except Exception as e:
        if "AccessDenied" in str(e):
            return {"error": "COH not enabled. Call get_enrollment_status first."}
        return {"error": str(e)}


def handle_get_recommendation(event):
    """Get detailed info for a specific recommendation."""
    rec_id = event.get("recommendation_id", "")
    if not rec_id:
        return {"error": "recommendation_id is required"}
    try:
        client = _get_client()
        r = client.get_recommendation(recommendationId=rec_id)
        return {
            "recommendation_id": r.get("recommendationId", ""),
            "account_id": r.get("accountId", ""),
            "region": r.get("region", ""),
            "resource_id": r.get("resourceId", ""),
            "resource_arn": r.get("resourceArn", ""),
            "current_resource_type": r.get("currentResourceType", ""),
            "recommended_resource_type": r.get("recommendedResourceType", ""),
            "estimated_monthly_savings": r.get("estimatedMonthlySavings", 0),
            "estimated_savings_percentage": r.get("estimatedSavingsPercentage", 0),
            "action_type": r.get("actionType", ""),
            "implementation_effort": r.get("implementationEffort", ""),
            "source": r.get("source", ""),
            "tags": r.get("tags", []),
        }
    except Exception as e:
        return {"error": str(e)}


def handle_list_recommendation_summaries(event):
    """Get aggregated savings summaries grouped by a dimension."""
    group_by = event.get("group_by", "ResourceType")
    try:
        client = _get_client()
        params = {
            "groupBy": group_by,
            "maxResults": min(event.get("max_results", 50), 1000),
        }

        filter_obj = {}
        for key, api_key in [
            ("resource_types", "resourceTypes"),
            ("action_types", "actionTypes"),
            ("regions", "regions"),
            ("account_ids", "accountIds"),
        ]:
            val = event.get(key)
            if val:
                filter_obj[api_key] = [val] if isinstance(val, str) else val
        if filter_obj:
            params["filter"] = filter_obj
        if event.get("next_token"):
            params["nextToken"] = event["next_token"]

        resp = client.list_recommendation_summaries(**params)
        summaries = sorted(
            [
                {
                    "group": i.get("group", ""),
                    "estimated_monthly_savings": i.get("estimatedMonthlySavings", 0),
                    "recommendation_count": i.get("recommendationCount", 0),
                }
                for i in resp.get("items", [])
            ],
            key=lambda x: x["estimated_monthly_savings"],
            reverse=True,
        )

        return {
            "group_by": group_by,
            "summaries": summaries,
            "count": len(summaries),
            "estimated_total_deduped_savings": resp.get(
                "estimatedTotalDedupedSavings", 0
            ),
        }
    except Exception as e:
        if "AccessDenied" in str(e):
            return {"error": "COH not enabled. Call get_enrollment_status first."}
        return {"error": str(e)}
