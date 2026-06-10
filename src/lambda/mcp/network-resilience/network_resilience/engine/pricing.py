"""DX + Network service pricing via the AWS Pricing API.

Python port of source ``dx-visualizer/src/chat/dx-pricing.ts`` (295 lines).

Live lookups against ``pricing:GetProducts`` — NOT hardcoded tables. The only
hardcoded pieces are ``HOURS_PER_MONTH = 730`` (standard billing month) and
the region-code → region-display-name map (Pricing API filters on
display names like "US East (N. Virginia)" rather than codes).

Pricing API is us-east-1 only (also ap-south-1 but we always call us-east-1).
"""

from __future__ import annotations

import json
import logging
from typing import Dict, Literal, Optional, TypedDict

logger = logging.getLogger(__name__)

HOURS_PER_MONTH = 730

# Service code → AWS Price List service code
_SERVICE_CODES = {
    "dx": "AWSDirectConnect",
    "tgw": "AmazonVPC",
    "vpn": "AmazonVPC",
    "vgw": "AmazonVPC",
}

# AWS region code → display name used by the Pricing API 'location' filter.
REGION_NAMES: Dict[str, str] = {
    "us-east-1": "US East (N. Virginia)",
    "us-east-2": "US East (Ohio)",
    "us-west-1": "US West (N. California)",
    "us-west-2": "US West (Oregon)",
    "eu-west-1": "EU (Ireland)",
    "eu-west-2": "EU (London)",
    "eu-central-1": "EU (Frankfurt)",
    "ap-southeast-1": "Asia Pacific (Singapore)",
    "ap-southeast-2": "Asia Pacific (Sydney)",
    "ap-northeast-1": "Asia Pacific (Tokyo)",
    "ap-northeast-2": "Asia Pacific (Seoul)",
    "ap-south-1": "Asia Pacific (Mumbai)",
    "sa-east-1": "South America (Sao Paulo)",
    "ca-central-1": "Canada (Central)",
    "me-south-1": "Middle East (Bahrain)",
    "af-south-1": "Africa (Cape Town)",
}


class ExtractedPrice(TypedDict, total=False):
    pricePerUnit: float
    unit: str
    description: str


def _extract_price(price_list_item: str) -> Optional[ExtractedPrice]:
    """Pull the first non-zero USD price out of a Pricing API product JSON."""
    try:
        product = json.loads(price_list_item)
    except Exception:  # noqa: BLE001
        return None
    terms = (product.get("terms") or {}).get("OnDemand")
    if not terms:
        return None
    for term_key in terms.keys():
        price_dimensions = (terms[term_key] or {}).get("priceDimensions") or {}
        for dim_key in price_dimensions.keys():
            dim = price_dimensions[dim_key] or {}
            usd_str = (dim.get("pricePerUnit") or {}).get("USD") or "0"
            try:
                usd = float(usd_str)
            except (TypeError, ValueError):
                continue
            if usd > 0:
                return {
                    "pricePerUnit": usd,
                    "unit": dim.get("unit") or "",
                    "description": dim.get("description") or "",
                }
    return None


# ----- DX port pricing ------------------------------------------------------


class DxPricingResult(TypedDict, total=False):
    monthlyPortCostPerConnection: float
    numConnections: int
    totalMonthlyPortCost: float
    dataTransferRatePerGb: float
    currency: Literal["USD"]
    notes: str


def lookup_dx_pricing(
    region: str, port_speed: str, num_connections: int = 1
) -> DxPricingResult:
    """Fetch DX port + data-transfer pricing for a region + port speed.

    Tries multiple capacity format variants because the Pricing API is
    inconsistent about "1Gbps" vs "1000Mbps" vs "1 Gbps". Returns structured
    zeroes and a ``notes`` field on failure rather than raising.
    """
    from ..topology import clients  # local import to avoid circular on engine/

    client = clients.pricing()
    region_name = REGION_NAMES.get(region, region)

    capacity_variants = {
        "1Gbps": ["1Gbps", "1000Mbps", "1 Gbps"],
        "10Gbps": ["10Gbps", "10 Gbps"],
        "100Gbps": ["100Gbps", "100 Gbps"],
    }
    variants = capacity_variants.get(port_speed, [port_speed])

    try:
        hourly_rate = 0.0
        for capacity in variants:
            port_resp = client.get_products(
                ServiceCode=_SERVICE_CODES["dx"],
                Filters=[
                    {"Type": "TERM_MATCH", "Field": "location", "Value": region_name},
                    {"Type": "TERM_MATCH", "Field": "capacity", "Value": capacity},
                ],
                MaxResults=10,
            )
            for item in port_resp.get("PriceList", []) or []:
                price = _extract_price(item)
                if price:
                    hourly_rate = price["pricePerUnit"]
                    break
            if hourly_rate > 0:
                break

        # Data transfer
        dt_resp = client.get_products(
            ServiceCode=_SERVICE_CODES["dx"],
            Filters=[
                {"Type": "TERM_MATCH", "Field": "fromLocation", "Value": region_name},
                {
                    "Type": "TERM_MATCH",
                    "Field": "productFamily",
                    "Value": "Data Transfer",
                },
            ],
            MaxResults=5,
        )
        dt_rate = 0.0
        for item in dt_resp.get("PriceList", []) or []:
            price = _extract_price(item)
            if price and price["pricePerUnit"] > 0:
                dt_rate = price["pricePerUnit"]
                break

        if hourly_rate == 0:
            return {
                "monthlyPortCostPerConnection": 0,
                "numConnections": num_connections,
                "totalMonthlyPortCost": 0,
                "dataTransferRatePerGb": dt_rate,
                "currency": "USD",
                "notes": (
                    f"AWS Pricing API returned no port pricing for "
                    f"{port_speed} in {region_name} — cannot answer right now."
                ),
            }

        monthly_per_conn = round(hourly_rate * HOURS_PER_MONTH, 2)
        return {
            "monthlyPortCostPerConnection": monthly_per_conn,
            "numConnections": num_connections,
            "totalMonthlyPortCost": round(monthly_per_conn * num_connections, 2),
            "dataTransferRatePerGb": dt_rate,
            "currency": "USD",
            "notes": (
                f"Live pricing for {region_name}, {port_speed} port "
                f"(${hourly_rate}/hr). Data transfer: ${dt_rate}/GB outbound."
            ),
        }
    except Exception as err:  # noqa: BLE001
        is_access_denied = (
            getattr(err, "response", {}).get("Error", {}).get("Code")
            == "AccessDeniedException"
            or "not authorized" in str(err).lower()
        )
        return {
            "monthlyPortCostPerConnection": 0,
            "numConnections": num_connections,
            "totalMonthlyPortCost": 0,
            "dataTransferRatePerGb": 0,
            "currency": "USD",
            "notes": (
                "Access denied: your IAM role does not have the "
                "`pricing:GetProducts` permission. Please add this permission "
                "to fetch live pricing."
                if is_access_denied
                else f"AWS Pricing API unavailable — cannot answer right now ({err})."
            ),
        }


# ----- Network service pricing (TGW / VPN / VGW) ----------------------------


class NetworkServicePricingResult(TypedDict, total=False):
    service: str
    region: str
    hourlyRate: float
    monthlyEstimate: float
    perGbRate: float
    currency: Literal["USD"]
    notes: str


def lookup_network_service_pricing(
    service: Literal["tgw", "vpn", "vgw"],
    region: str,
    num_attachments: int = 1,
) -> NetworkServicePricingResult:
    """Hourly + data-processing pricing for TGW / VPN. VGW returns 0s (no
    per-hour charge — cost comes from attached VPNs/VIFs).
    """
    if service == "vgw":
        return {
            "service": "Virtual Private Gateway (VGW)",
            "region": region,
            "hourlyRate": 0,
            "monthlyEstimate": 0,
            "perGbRate": 0,
            "currency": "USD",
            "notes": (
                "VGW has no hourly charge. Costs come from attached VPN "
                "connections or Direct Connect VIFs."
            ),
        }

    from ..topology import clients

    client = clients.pricing()
    region_name = REGION_NAMES.get(region, region)

    try:
        if service == "tgw":
            # TGW attachment hourly
            attach_resp = client.get_products(
                ServiceCode=_SERVICE_CODES["tgw"],
                Filters=[
                    {"Type": "TERM_MATCH", "Field": "location", "Value": region_name},
                    {"Type": "TERM_MATCH", "Field": "group", "Value": "AWSTransitGateway"},
                    {
                        "Type": "TERM_MATCH",
                        "Field": "groupDescription",
                        "Value": "TransitGateway Attachment",
                    },
                ],
                MaxResults=5,
            )
            attach_hourly = 0.0
            for item in attach_resp.get("PriceList", []) or []:
                price = _extract_price(item)
                if price:
                    attach_hourly = price["pricePerUnit"]
                    break

            # TGW data processing
            data_resp = client.get_products(
                ServiceCode=_SERVICE_CODES["tgw"],
                Filters=[
                    {"Type": "TERM_MATCH", "Field": "location", "Value": region_name},
                    {"Type": "TERM_MATCH", "Field": "group", "Value": "AWSTransitGateway"},
                    {
                        "Type": "TERM_MATCH",
                        "Field": "groupDescription",
                        "Value": "TransitGateway Data Processing",
                    },
                ],
                MaxResults=5,
            )
            data_per_gb = 0.0
            for item in data_resp.get("PriceList", []) or []:
                price = _extract_price(item)
                if price:
                    data_per_gb = price["pricePerUnit"]
                    break

            total_hourly = attach_hourly * num_attachments
            monthly = round(total_hourly * HOURS_PER_MONTH, 2)
            return {
                "service": "Transit Gateway (TGW)",
                "region": region_name,
                "hourlyRate": total_hourly,
                "monthlyEstimate": monthly,
                "perGbRate": data_per_gb,
                "currency": "USD",
                "notes": (
                    f"Live pricing: ${attach_hourly}/hr per attachment × "
                    f"{num_attachments}. Data processing: ${data_per_gb}/GB."
                ),
            }

        # VPN
        vpn_resp = client.get_products(
            ServiceCode=_SERVICE_CODES["vpn"],
            Filters=[
                {"Type": "TERM_MATCH", "Field": "location", "Value": region_name},
                {"Type": "TERM_MATCH", "Field": "group", "Value": "VPNConnection"},
            ],
            MaxResults=5,
        )
        vpn_hourly = 0.0
        for item in vpn_resp.get("PriceList", []) or []:
            price = _extract_price(item)
            if price:
                vpn_hourly = price["pricePerUnit"]
                break

        total_hourly = vpn_hourly * num_attachments
        monthly = round(total_hourly * HOURS_PER_MONTH, 2)
        return {
            "service": "Site-to-Site VPN",
            "region": region_name,
            "hourlyRate": total_hourly,
            "monthlyEstimate": monthly,
            "perGbRate": 0,
            "currency": "USD",
            "notes": (
                f"Live pricing: ${vpn_hourly}/hr per VPN connection × "
                f"{num_attachments}."
            ),
        }
    except Exception as err:  # noqa: BLE001
        is_access_denied = (
            getattr(err, "response", {}).get("Error", {}).get("Code")
            == "AccessDeniedException"
            or "not authorized" in str(err).lower()
        )
        return {
            "service": (
                "Transit Gateway (TGW)"
                if service == "tgw"
                else "Site-to-Site VPN"
            ),
            "region": region,
            "hourlyRate": 0,
            "monthlyEstimate": 0,
            "perGbRate": 0,
            "currency": "USD",
            "notes": (
                "Access denied: your IAM role does not have the "
                "`pricing:GetProducts` permission. Please add this permission "
                "to fetch live pricing."
                if is_access_denied
                else f"Failed to fetch pricing: {err}"
            ),
        }
