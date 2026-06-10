"""
AWS Billing MCP Server - Lambda Implementation for AgentCore Gateway

Provides tools for billing operations: anomaly detection, budget alerts,
and account information.

Tools (3):
- get_anomalies: Cost anomalies detected by AWS Cost Anomaly Detection
- get_billing_alerts: Active billing alerts/budgets and their status
- get_account_info: Account billing preferences and payment info

Required IAM Permissions:
- ce:GetAnomalies
- budgets:DescribeBudgets
- account:GetContactInformation (optional)
"""

import json
from datetime import datetime, timedelta, timezone

import boto3


def handler(event, context):
    print(f"Event: {json.dumps(event)}")
    extended_tool_name = context.client_context.custom["bedrockAgentCoreToolName"]
    tool_name = extended_tool_name.split("___")[1]
    print(f"Tool name: {tool_name}")

    handlers = {
        "get_anomalies": handle_get_anomalies,
        "get_billing_alerts": handle_get_billing_alerts,
        "get_account_info": handle_get_account_info,
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


def handle_get_anomalies(event):
    """Get cost anomalies detected by AWS Cost Anomaly Detection."""
    now = datetime.now(timezone.utc)
    days_back = event.get("days_back", 30)
    start_date = (now - timedelta(days=days_back)).strftime("%Y-%m-%d")
    end_date = now.strftime("%Y-%m-%d")

    ce = boto3.client("ce")
    try:
        resp = ce.get_anomalies(
            DateInterval={"StartDate": start_date, "EndDate": end_date},
            MaxResults=min(event.get("max_results", 20), 100),
        )
        anomalies = []
        for a in resp.get("Anomalies", []):
            impact = a.get("Impact", {})
            anomalies.append(
                {
                    "anomaly_id": a.get("AnomalyId", ""),
                    "start_date": a.get("AnomalyStartDate", ""),
                    "end_date": a.get("AnomalyEndDate", ""),
                    "dimension_value": a.get("DimensionValue", ""),
                    "max_impact": round(float(impact.get("MaxImpact", 0)), 2),
                    "total_impact": round(float(impact.get("TotalImpact", 0)), 2),
                    "total_actual_spend": round(
                        float(impact.get("TotalActualSpend", 0)), 2
                    ),
                    "total_expected_spend": round(
                        float(impact.get("TotalExpectedSpend", 0)), 2
                    ),
                    "feedback": a.get("Feedback", ""),
                }
            )
        return {
            "anomalies": anomalies,
            "count": len(anomalies),
            "period": {"start": start_date, "end": end_date},
        }
    except Exception as e:
        if "UnknownMonitor" in str(e) or "not subscribed" in str(e).lower():
            return {
                "anomalies": [],
                "count": 0,
                "message": "Cost Anomaly Detection is not configured. Set up a monitor in the AWS console.",
            }
        return {"error": str(e)}


def handle_get_billing_alerts(event):
    """Get active AWS Budgets and their current status."""
    try:
        account_id = boto3.client("sts").get_caller_identity()["Account"]
        budgets = boto3.client("budgets")
        resp = budgets.describe_budgets(AccountId=account_id, MaxResults=50)
        alerts = []
        for b in resp.get("Budgets", []):
            spent = b.get("CalculatedSpend", {})
            actual = float(spent.get("ActualSpend", {}).get("Amount", 0))
            forecast = float(spent.get("ForecastedSpend", {}).get("Amount", 0))
            limit = float(b.get("BudgetLimit", {}).get("Amount", 0))
            alerts.append(
                {
                    "name": b.get("BudgetName", ""),
                    "type": b.get("BudgetType", ""),
                    "limit": round(limit, 2),
                    "actual_spend": round(actual, 2),
                    "forecasted_spend": round(forecast, 2),
                    "utilization_pct": round(
                        (actual / limit * 100) if limit > 0 else 0, 1
                    ),
                    "time_unit": b.get("TimeUnit", ""),
                    "period": str(b.get("TimePeriod", {}).get("Start", "")),
                }
            )
        return {"budgets": alerts, "count": len(alerts)}
    except Exception as e:
        if "AccessDenied" in str(e):
            return {
                "budgets": [],
                "count": 0,
                "message": "No budget access or no budgets configured.",
            }
        return {"error": str(e)}


def handle_get_account_info(event):
    """Get basic account billing info — account ID, aliases, and billing contact."""
    try:
        sts = boto3.client("sts")
        identity = sts.get_caller_identity()
        iam = boto3.client("iam")
        aliases = iam.list_account_aliases().get("AccountAliases", [])
        result = {
            "account_id": identity["Account"],
            "account_aliases": aliases,
            "arn": identity["Arn"],
        }
        # Try to get organization info
        try:
            org = boto3.client("organizations")
            account = org.describe_account(AccountId=identity["Account"])["Account"]
            result["account_name"] = account.get("Name", "")
            result["account_email"] = account.get("Email", "")
            result["account_status"] = account.get("Status", "")
        except Exception:
            pass
        return result
    except Exception as e:
        return {"error": str(e)}
