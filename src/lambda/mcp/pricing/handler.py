"""
AWS Pricing MCP Server - Lambda Implementation for AgentCore Gateway

Provides tools to query AWS service pricing from the Pricing API.
The Pricing API is only available in us-east-1 and ap-south-1.

Tools (3):
- list_services: List available AWS services in the pricing catalog
- get_service_pricing: Get pricing for a specific service with filters
- get_attribute_values: Get valid filter values for a service attribute

Required IAM Permissions:
- pricing:GetProducts
- pricing:DescribeServices
- pricing:GetAttributeValues
"""

import json
import boto3

# Pricing API is only available in us-east-1 and ap-south-1
PRICING_REGION = "us-east-1"


def _get_client():
    return boto3.client("pricing", region_name=PRICING_REGION)


def handler(event, context):
    print(f"Event: {json.dumps(event)}")
    extended_tool_name = context.client_context.custom["bedrockAgentCoreToolName"]
    tool_name = extended_tool_name.split("___")[1]
    print(f"Tool name: {tool_name}")

    handlers = {
        "list_services": handle_list_services,
        "get_service_pricing": handle_get_service_pricing,
        "get_attribute_values": handle_get_attribute_values,
    }
    fn = handlers.get(tool_name)
    if fn:
        resp = fn(event)
        print(
            f"Response keys: {list(resp.keys()) if isinstance(resp, dict) else 'n/a'}"
        )
        return resp
    return {
        "error": f"Unknown tool: {tool_name}",
        "available_tools": list(handlers.keys()),
    }


def handle_list_services(event):
    """List available AWS services in the pricing catalog."""
    try:
        client = _get_client()
        services = []
        kwargs = {}
        if event.get("next_token"):
            kwargs["NextToken"] = event["next_token"]
        resp = client.describe_services(
            FormatVersion="aws_v1",
            MaxResults=min(event.get("max_results", 50), 100),
            **kwargs,
        )
        for svc in resp.get("Services", []):
            services.append(
                {
                    "service_code": svc.get("ServiceCode", ""),
                    "attribute_names": svc.get("AttributeNames", [])[:10],
                }
            )
        result = {"services": services, "count": len(services)}
        if resp.get("NextToken"):
            result["next_token"] = resp["NextToken"]
        return result
    except Exception as e:
        return {"error": str(e)}


def handle_get_service_pricing(event):
    """Get pricing for a specific AWS service with optional filters.

    Filters are key-value pairs matching service attributes.
    Common filters: regionCode, instanceType, operatingSystem, tenancy.
    """
    service_code = event.get("service_code", "")
    if not service_code:
        return {
            "error": "service_code is required (e.g., AmazonEC2, AmazonRDS, AmazonS3)"
        }

    try:
        client = _get_client()
        filters = []
        for key, value in event.get("filters", {}).items():
            filters.append(
                {
                    "Type": "TERM_MATCH",
                    "Field": key,
                    "Value": value,
                }
            )

        kwargs = {
            "ServiceCode": service_code,
            "FormatVersion": "aws_v1",
            "MaxResults": min(event.get("max_results", 10), 100),
        }
        if filters:
            kwargs["Filters"] = filters
        if event.get("next_token"):
            kwargs["NextToken"] = event["next_token"]

        resp = client.get_products(**kwargs)
        products = []
        for price_json in resp.get("PriceList", []):
            try:
                price_data = (
                    json.loads(price_json)
                    if isinstance(price_json, str)
                    else price_json
                )
                product = price_data.get("product", {})
                terms = price_data.get("terms", {})

                # Extract on-demand pricing
                on_demand = {}
                for term_id, term_data in terms.get("OnDemand", {}).items():
                    for dim_id, dim_data in term_data.get(
                        "priceDimensions", {}
                    ).items():
                        price_per_unit = dim_data.get("pricePerUnit", {})
                        usd = price_per_unit.get("USD", "0")
                        if float(usd) > 0:
                            on_demand = {
                                "price_usd": usd,
                                "unit": dim_data.get("unit", ""),
                                "description": dim_data.get("description", ""),
                            }
                            break
                    if on_demand:
                        break

                products.append(
                    {
                        "sku": product.get("sku", ""),
                        "family": product.get("productFamily", ""),
                        "attributes": {
                            k: v
                            for k, v in product.get("attributes", {}).items()
                            if k
                            in (
                                "instanceType",
                                "regionCode",
                                "operatingSystem",
                                "tenancy",
                                "vcpu",
                                "memory",
                                "storage",
                                "servicecode",
                                "usagetype",
                                "location",
                            )
                        },
                        "on_demand_pricing": on_demand,
                    }
                )
            except Exception:
                continue

        result = {
            "service_code": service_code,
            "products": products,
            "count": len(products),
        }
        if resp.get("NextToken"):
            result["next_token"] = resp["NextToken"]
        return result
    except Exception as e:
        return {"error": str(e)}


def handle_get_attribute_values(event):
    """Get valid values for a service attribute (useful for building filters)."""
    service_code = event.get("service_code", "")
    attribute_name = event.get("attribute_name", "")
    if not service_code or not attribute_name:
        return {"error": "service_code and attribute_name are required"}

    try:
        client = _get_client()
        kwargs = {
            "ServiceCode": service_code,
            "AttributeName": attribute_name,
            "MaxResults": min(event.get("max_results", 50), 100),
        }
        if event.get("next_token"):
            kwargs["NextToken"] = event["next_token"]

        resp = client.get_attribute_values(**kwargs)
        values = [v.get("Value", "") for v in resp.get("AttributeValues", [])]
        result = {
            "service_code": service_code,
            "attribute_name": attribute_name,
            "values": values,
            "count": len(values),
        }
        if resp.get("NextToken"):
            result["next_token"] = resp["NextToken"]
        return result
    except Exception as e:
        return {"error": str(e)}
