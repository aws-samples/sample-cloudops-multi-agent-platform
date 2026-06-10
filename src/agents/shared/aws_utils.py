"""Shared AWS utilities for cross-account role assumption.

Provides a unified interface for obtaining boto3 clients that optionally
assume a cross-account IAM role via STS when CROSS_ACCOUNT_ROLE_ARN is set.
"""

import os

import boto3
from botocore.exceptions import ClientError


class CrossAccountAccessError(Exception):
    """Raised when STS AssumeRole fails for cross-account access."""


def extract_account_from_arn(arn: str) -> str:
    """Extract the AWS account ID from an ARN.

    ARN format: arn:partition:service:region:account-id:resource
    """
    parts = arn.split(":")
    if len(parts) >= 5:
        return parts[4]
    return "unknown"


def get_aws_client(service_name: str, region_name: str = None) -> boto3.client:
    """Get a boto3 client with optional cross-account role assumption.

    If CROSS_ACCOUNT_ROLE_ARN is set, assumes the role via STS and returns
    a client using temporary credentials. Otherwise returns a default client.

    Args:
        service_name: The AWS service name (e.g. 's3', 'sts', 'ce').
        region_name: Optional AWS region override.

    Returns:
        A boto3 client for the requested service.

    Raises:
        CrossAccountAccessError: If STS AssumeRole fails.
    """
    cross_account_role = os.environ.get("CROSS_ACCOUNT_ROLE_ARN", "")

    if cross_account_role:
        agent_name = os.environ.get("AGENT_NAME", "mcp")
        request_id = os.environ.get("REQUEST_ID", "unknown")
        session_name = f"{agent_name}-{request_id}"

        try:
            sts = boto3.client("sts", region_name=region_name)
            credentials = sts.assume_role(
                RoleArn=cross_account_role,
                RoleSessionName=session_name,
                DurationSeconds=3600,
            )["Credentials"]
        except ClientError as exc:
            account_id = extract_account_from_arn(cross_account_role)
            error_reason = str(exc)
            raise CrossAccountAccessError(
                f"Cross-account access failure for account {account_id}: {error_reason}"
            ) from exc

        kwargs = {
            "aws_access_key_id": credentials["AccessKeyId"],
            "aws_secret_access_key": credentials["SecretAccessKey"],
            "aws_session_token": credentials["SessionToken"],
        }
        if region_name:
            kwargs["region_name"] = region_name
        return boto3.client(service_name, **kwargs)

    kwargs = {}
    if region_name:
        kwargs["region_name"] = region_name
    return boto3.client(service_name, **kwargs)
