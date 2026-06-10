"""Unit tests for agents.shared.aws_utils cross-account role assumption utility."""

import os
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from agents.shared.aws_utils import (
    CrossAccountAccessError,
    extract_account_from_arn,
    get_aws_client,
)


class TestExtractAccountFromArn:
    def test_standard_role_arn(self):
        arn = "arn:aws:iam::123456789012:role/MyRole"
        assert extract_account_from_arn(arn) == "123456789012"

    def test_govcloud_arn(self):
        arn = "arn:aws-us-gov:iam::111222333444:role/GovRole"
        assert extract_account_from_arn(arn) == "111222333444"

    def test_malformed_arn_returns_unknown(self):
        assert extract_account_from_arn("not-an-arn") == "unknown"

    def test_empty_string_returns_unknown(self):
        assert extract_account_from_arn("") == "unknown"


class TestGetAwsClientDefaultCredentials:
    @patch.dict(os.environ, {}, clear=True)
    @patch("agents.shared.aws_utils.boto3")
    def test_returns_default_client_when_no_role_arn(self, mock_boto3):
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        result = get_aws_client("s3")
        mock_boto3.client.assert_called_once_with("s3")
        assert result is mock_client

    @patch.dict(os.environ, {"CROSS_ACCOUNT_ROLE_ARN": ""}, clear=True)
    @patch("agents.shared.aws_utils.boto3")
    def test_returns_default_client_when_role_arn_empty(self, mock_boto3):
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        result = get_aws_client("ce")
        mock_boto3.client.assert_called_once_with("ce")
        assert result is mock_client

    @patch.dict(os.environ, {}, clear=True)
    @patch("agents.shared.aws_utils.boto3")
    def test_passes_region_name(self, mock_boto3):
        get_aws_client("s3", region_name="eu-west-1")
        mock_boto3.client.assert_called_once_with("s3", region_name="eu-west-1")


class TestGetAwsClientCrossAccount:
    ROLE_ARN = "arn:aws:iam::123456789012:role/CrossAccountRole"
    FAKE_CREDS = {
        "Credentials": {
            "AccessKeyId": "TEST-FAKE-ACCESS-KEY",
            "SecretAccessKey": "test-fake-secret-access-key",
            "SessionToken": "test-fake-session-token",
        }
    }

    @patch.dict(
        os.environ,
        {
            "CROSS_ACCOUNT_ROLE_ARN": ROLE_ARN,
            "AGENT_NAME": "finops",
            "REQUEST_ID": "req-123",
        },
        clear=True,
    )
    @patch("agents.shared.aws_utils.boto3")
    def test_assumes_role_and_returns_client(self, mock_boto3):
        mock_sts = MagicMock()
        mock_sts.assume_role.return_value = self.FAKE_CREDS
        mock_service_client = MagicMock()
        mock_boto3.client.side_effect = [mock_sts, mock_service_client]
        result = get_aws_client("ce")
        mock_sts.assume_role.assert_called_once_with(
            RoleArn=self.ROLE_ARN,
            RoleSessionName="finops-req-123",
            DurationSeconds=3600,
        )
        assert result is mock_service_client

    @patch.dict(os.environ, {"CROSS_ACCOUNT_ROLE_ARN": ROLE_ARN}, clear=True)
    @patch("agents.shared.aws_utils.boto3")
    def test_raises_cross_account_error_on_sts_failure(self, mock_boto3):
        mock_sts = MagicMock()
        mock_sts.assume_role.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "Not authorized"}},
            "AssumeRole",
        )
        mock_boto3.client.return_value = mock_sts
        with pytest.raises(CrossAccountAccessError) as exc_info:
            get_aws_client("s3")
        assert "123456789012" in str(exc_info.value)
