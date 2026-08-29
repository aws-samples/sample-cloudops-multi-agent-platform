from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODULE = (
    ROOT / "terraform" / "modules" / "custom" / "devops-agent-integration" / "main.tf"
).read_text()
READ_API_MODULE = (
    ROOT / "terraform" / "modules" / "custom" / "devops-agent-read-api" / "main.tf"
).read_text()
FRONTEND_API_MODULE = (
    ROOT / "terraform" / "modules" / "core" / "frontend-api" / "main.tf"
).read_text()
ROOT_TF = (ROOT / "terraform" / "main.tf").read_text()
HEALTH_TF = (
    ROOT / "terraform" / "modules" / "custom" / "health-events-collection" / "main.tf"
).read_text()
CONFIG_COMMANDS = (ROOT / "scripts" / "lib" / "commands.sh").read_text()


def test_health_stream_uses_new_and_old_images():
    assert 'stream_view_type = "NEW_AND_OLD_IMAGES"' in HEALTH_TF


def test_workflow_has_bounded_stream_failures_and_event_dlq():
    for required in (
        'function_response_types        = ["ReportBatchItemFailures"]',
        "bisect_batch_on_function_error = true",
        "maximum_retry_attempts         = 3",
        "maximum_record_age_in_seconds  = 3600",
        "dead_letter_config",
        "retry_policy",
        "reconcile_dlq",
    ):
        assert required in MODULE


def test_workflow_lambda_updates_use_content_addressed_s3_artifact():
    for required in (
        'resource "aws_s3_object" "workflow"',
        "filesha256(var.workflow_zip_path)",
        "source_hash = filebase64sha256(var.workflow_zip_path)",
        "s3_bucket         = aws_s3_object.workflow.bucket",
        "s3_key            = aws_s3_object.workflow.key",
        "s3_object_version = aws_s3_object.workflow.version_id",
        "artifact_bucket            = var.s3_bucket",
    ):
        assert required in MODULE or required in ROOT_TF
    assert "filename         = var.workflow_zip_path" not in MODULE


def test_workflow_dynamodb_permissions_are_handler_specific():
    assert "workflow_dynamodb_actions" in MODULE
    stream_actions = MODULE.split("stream = [", 1)[1].split("]", 1)[0]
    assert "dynamodb:DeleteItem" not in stream_actions
    assert "dynamodb:PutItem" in stream_actions
    assert "dynamodb:TransactWriteItems" in stream_actions
    assert "dynamodb:BatchGetItem" not in MODULE
    assert "dynamodb:BatchWriteItem" not in MODULE

    gateway_actions = ROOT_TF.split(
        'resource "aws_iam_role_policy" "devops_agent_gateway"', 1
    )[1].split('resource "aws_iam_role_policy" "health_investigation_read"', 1)[0]
    assert "dynamodb:PutItem" in gateway_actions
    assert "dynamodb:Query" not in gateway_actions
    assert "dynamodb:BatchWriteItem" not in gateway_actions
    assert "dynamodb:TransactWriteItems" in gateway_actions


def test_exact_aidevops_actions_and_agent_space_arn():
    actions = {
        "aidevops:CreateBacklogTask",
        "aidevops:GetBacklogTask",
        "aidevops:ListJournalRecords",
        "aidevops:ListAssociations",
        "aidevops:UpdateBacklogTask",
    }
    for action in actions:
        assert action in MODULE
    for gateway_action in (
        "aidevops:CreateBacklogTask",
        "aidevops:ListJournalRecords",
        "aidevops:ListAssociations",
    ):
        assert gateway_action in ROOT_TF
    gateway_policy = ROOT_TF.split(
        'resource "aws_iam_role_policy" "devops_agent_gateway"', 1
    )[1].split('resource "aws_iam_role_policy" "health_investigation_read"', 1)[0]
    assert "aidevops:GetBacklogTask" not in gateway_policy
    assert "aidevops:UpdateBacklogTask" not in gateway_policy
    assert "agentspace/${var.agent_space_id}" in MODULE
    assert "aidevops:*" not in MODULE
    assert "aidevops:*" not in ROOT_TF


def test_agent_runtime_modules_receive_no_aidevops_permissions():
    runtime_modules = (
        ROOT / "terraform" / "modules" / "core" / "agent-runtime-base" / "main.tf"
    ).read_text()
    assert "aidevops:" not in runtime_modules


def test_investigation_table_kms_permissions_are_scoped_to_configured_key():
    workflow_policy = MODULE.split('resource "aws_iam_role_policy" "workflow_base"', 1)[
        1
    ].split('resource "aws_iam_role_policy" "stream_source"', 1)[0]
    assert 'var.kms_key_arn != ""' in workflow_policy
    assert "Resource = var.kms_key_arn" in workflow_policy
    for action in ("kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"):
        assert action in workflow_policy

    gateway_policy = ROOT_TF.split(
        'resource "aws_iam_role_policy" "devops_agent_gateway"', 1
    )[1].split('resource "aws_iam_role_policy" "health_investigation_read"', 1)[0]
    assert "Resource = module.kms.key_arn" in gateway_policy
    for action in ("kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"):
        assert action in gateway_policy

    health_read_policy = ROOT_TF.split(
        'resource "aws_iam_role_policy" "health_investigation_read"', 1
    )[1].split("# Keep the generic tool role policy", 1)[0]
    assert 'Action   = ["kms:Decrypt"]' in health_read_policy
    assert "Resource = module.kms.key_arn" in health_read_policy


def test_browser_api_reads_table_and_invokes_scoped_refresh():
    for action in ("dynamodb:GetItem", "kms:Decrypt"):
        assert action in READ_API_MODULE
    assert 'Action   = ["lambda:InvokeFunction"]' in READ_API_MODULE
    assert "Resource = var.reconcile_function_arn" in READ_API_MODULE
    for forbidden in (
        "dynamodb:Query",
        "dynamodb:PutItem",
        "dynamodb:UpdateItem",
        "dynamodb:DeleteItem",
    ):
        assert forbidden not in READ_API_MODULE
    assert "aidevops:" not in READ_API_MODULE
    assert "kms:GenerateDataKey" not in READ_API_MODULE


def test_browser_read_api_reuses_frontend_authorizer():
    for required in (
        'route_key          = "GET /devops-agent/investigations/{investigationId}"',
        'route_key          = "POST /devops-agent/investigations/{investigationId}"',
        'authorization_type = "JWT"',
        "authorizer_id      = var.cognito_authorizer_id",
        'source = "./modules/custom/devops-agent-read-api"',
        "cognito_authorizer_id     = module.frontend_api[0].cognito_authorizer_id",
        "reconcile_function_arn    = module.devops_agent_integration[0].reconcile_function_arn",
    ):
        assert required in READ_API_MODULE or required in ROOT_TF


def test_frontend_api_cors_supports_conditional_investigation_reads():
    assert '"If-None-Match"' in FRONTEND_API_MODULE
    assert 'expose_headers = ["ETag"]' in FRONTEND_API_MODULE


def test_eventbridge_rule_includes_mitigation_lifecycle():
    for detail_type in (
        "Mitigation In Progress",
        "Mitigation Completed",
        "Mitigation Failed",
        "Mitigation Timed Out",
        "Mitigation Cancelled",
    ):
        assert f'"{detail_type}"' in MODULE


def test_frontend_runtime_can_hydrate_investigation_context():
    runtime = (
        ROOT_TF.split('module "agentcore_runtime"', 1)[1]
        .split('module "agentcore_memory"', 1)[0]
    )
    runtime_module = (
        ROOT / "terraform" / "modules" / "core" / "agentcore-runtime" / "main.tf"
    ).read_text()
    assert "investigations_table_name" in runtime
    assert "investigations_table_arn" in runtime
    assert "INVESTIGATIONS_TABLE_NAME" in runtime_module
    assert '"dynamodb:BatchGetItem"' in runtime_module


def test_generic_configuration_prompts_for_agent_space_id():
    all_tools = CONFIG_COMMANDS.split('active_tools="cost-explorer,', 1)[1].split(
        '"', 1
    )[0]
    assert "devops-agent" in all_tools
    assert 'shared_config_prompt devops_space_id "  Agent Space ID"' in CONFIG_COMMANDS
    assert 'if [ -z "$devops_space_id" ]' in CONFIG_COMMANDS
    assert "_answers_set DEVOPS_AGENT_SPACE_ID" in CONFIG_COMMANDS


def test_core_integration_is_independent_from_health_stream_ingestion():
    integration = ROOT_TF.split('module "devops_agent_integration"', 1)[1].split(
        'module "tag_governance_collection"', 1
    )[0]
    assert 'contains(var.selected_tools, "devops-agent")' in integration
    assert "length(module.health_events_collection) > 0" not in integration.split(
        "project_tag", 1
    )[0]
    assert 'try(module.health_events_collection[0].stream_arn, "")' in integration
    assert (
        "var.devops_agent_health_automatic_enabled "
        "&& length(module.health_events_collection) > 0"
    ) in integration
    assert (
        "var.automatic_health_enabled && var.health_table_stream_arn"
    ) in MODULE
