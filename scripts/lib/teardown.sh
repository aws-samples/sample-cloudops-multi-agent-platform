#!/usr/bin/env bash
# Teardown functions — ECR cleanup, memory cleanup, state backend cleanup
cleanup_ecr_repos() {
  info "Cleaning up ECR repositories..."
  local repos
  repos=$(aws ecr describe-repositories \
    --query "repositories[?starts_with(repositoryName, \`${PROJECT_PREFIX}-${ENVIRONMENT}-\`)].repositoryName" \
    --region "$AWS_REGION" --output text 2>/dev/null || echo "")

  if [ -z "$repos" ]; then
    info "No ECR repositories found"
    return
  fi

  for repo in $repos; do
    info "Deleting ECR repo: $repo"
    aws ecr delete-repository --repository-name "$repo" --force --region "$AWS_REGION" > /dev/null 2>&1 \
      || warn "Failed to delete ECR repo: $repo"
  done
  info "ECR cleanup complete"
}

cleanup_memory_resource() {
  # Memory is now managed by Terraform, so terraform destroy handles it.
  # This is a safety net for orphaned resources when Terraform state is lost.
  info "Checking for orphaned AgentCore Memory resources..."
  .venv/bin/python -c "
import boto3, sys
client = boto3.client('bedrock-agentcore-control', region_name='$AWS_REGION')
try:
    resp = client.list_memories()
    memories = resp.get('memories', [])
    target_prefix = '${PROJECT_PREFIX}_${ENVIRONMENT}_memory'.replace('-', '_')
    for m in memories:
        if m.get('name', '').startswith(target_prefix):
            mid = m['memoryId']
            print(f'Deleting orphaned memory: {mid} ({m[\"name\"]})')
            client.delete_memory(memoryId=mid)
            print(f'Deleted {mid}')
    if not any(m.get('name', '').startswith(target_prefix) for m in memories):
        print('No orphaned memory resources found')
except Exception as e:
    print(f'Memory cleanup: {e}', file=sys.stderr)
" 2>&1 || warn "Memory cleanup failed (non-fatal)"
}

cleanup_agentcore_log_groups() {
  info "Cleaning up AgentCore Runtime log groups..."
  local prefix_underscore="/aws/bedrock-agentcore/runtimes/$(echo "${PROJECT_PREFIX}" | tr '-' '_')_"
  .venv/bin/python -c "
import boto3
client = boto3.client('logs', region_name='${AWS_REGION}')
prefix = '${prefix_underscore}'
total = 0
kwargs = {'logGroupNamePrefix': prefix, 'limit': 50}
while True:
    resp = client.describe_log_groups(**kwargs)
    groups = [g['logGroupName'] for g in resp.get('logGroups', [])]
    for lg in groups:
        client.delete_log_group(logGroupName=lg)
        total += 1
    if 'nextToken' not in resp or not groups:
        break
    kwargs['nextToken'] = resp['nextToken']
if total:
    print(f'Deleted {total} AgentCore log group(s)')
else:
    print('No AgentCore log groups found')
" 2>&1 || warn "AgentCore log group cleanup failed (non-fatal)"
}

# Also clean up observability delivery sources/destinations created by enable_observability
cleanup_observability_deliveries() {
  info "Cleaning up observability delivery pipelines..."
  local prefix="${PROJECT_PREFIX}"
  .venv/bin/python -c "
import boto3
client = boto3.client('logs', region_name='$AWS_REGION')
# Delete deliveries
try:
    resp = client.describe_deliveries(limit=50)
    for d in resp.get('deliveries', []):
        if '${prefix}' in d.get('deliverySourceName', ''):
            client.delete_delivery(id=d['id'])
            print(f'  Deleted delivery: {d[\"id\"]}')
except Exception as e:
    print(f'  Delivery cleanup: {e}')
# Delete delivery destinations
try:
    resp = client.describe_delivery_destinations(limit=50)
    for d in resp.get('deliveryDestinations', []):
        if '${prefix}' in d.get('name', ''):
            client.delete_delivery_destination(name=d['name'])
            print(f'  Deleted destination: {d[\"name\"]}')
except Exception as e:
    print(f'  Destination cleanup: {e}')
# Delete delivery sources
try:
    resp = client.describe_delivery_sources(limit=50)
    for s in resp.get('deliverySources', []):
        if '${prefix}' in s.get('name', ''):
            client.delete_delivery_source(name=s['name'])
            print(f'  Deleted source: {s[\"name\"]}')
except Exception as e:
    print(f'  Source cleanup: {e}')
" 2>&1 || warn "Observability cleanup failed (non-fatal)"
}

cleanup_state_backend() {
  info "Cleaning up Terraform state backend..."

  # Delete all object versions from the S3 state bucket
  if aws s3api head-bucket --bucket "$S3_BUCKET" > /dev/null 2>&1; then
    info "Emptying state bucket: $S3_BUCKET (including versions)..."
    .venv/bin/python -c "
import boto3
session = boto3.Session(region_name='$AWS_REGION')
s3 = session.resource('s3')
bucket = s3.Bucket('$S3_BUCKET')
bucket.object_versions.all().delete()
print('All versions deleted')
" 2>&1 || warn "Failed to empty bucket versions"

    info "Deleting state bucket: $S3_BUCKET"
    aws s3 rb "s3://$S3_BUCKET" --region "$AWS_REGION" 2>&1 \
      || warn "Failed to delete state bucket"
  else
    info "State bucket $S3_BUCKET does not exist"
  fi

  # Delete the DynamoDB lock table
  if aws dynamodb describe-table --table-name "$DYNAMODB_TABLE" --region "$AWS_REGION" > /dev/null 2>&1; then
    info "Deleting lock table: $DYNAMODB_TABLE"
    aws dynamodb delete-table --table-name "$DYNAMODB_TABLE" --region "$AWS_REGION" > /dev/null 2>&1 \
      || warn "Failed to delete lock table"
  else
    info "Lock table $DYNAMODB_TABLE does not exist"
  fi

  # Delete the bootstrap CloudFormation stack if it exists
  local stack_name="${PROJECT_PREFIX}-${ENVIRONMENT}-tf-bootstrap"
  if aws cloudformation describe-stacks --stack-name "$stack_name" --region "$AWS_REGION" > /dev/null 2>&1; then
    info "Deleting bootstrap stack: $stack_name"
    aws cloudformation delete-stack --stack-name "$stack_name" --region "$AWS_REGION" 2>&1 \
      || warn "Failed to delete bootstrap stack"
  fi

  # Clean local Terraform files
  rm -rf "$TERRAFORM_DIR/.terraform" "$TERRAFORM_DIR/.terraform.lock.hcl" "$TERRAFORM_DIR/terraform.tfvars"
  rm -rf .lambda-hashes/*.sha

  info "State backend cleanup complete"
}

run_full_destroy() {
  # Step 1: Terraform destroy (if state backend is accessible)
  if aws s3api head-bucket --bucket "$S3_BUCKET" > /dev/null 2>&1 \
     && aws dynamodb describe-table --table-name "$DYNAMODB_TABLE" --region "$AWS_REGION" > /dev/null 2>&1; then
    run_terraform destroy
  else
    # Recreate lock table if missing so terraform can acquire the lock
    if ! aws dynamodb describe-table --table-name "$DYNAMODB_TABLE" --region "$AWS_REGION" > /dev/null 2>&1; then
      if aws s3api head-bucket --bucket "$S3_BUCKET" > /dev/null 2>&1; then
        warn "Lock table missing, recreating for destroy..."
        aws dynamodb create-table \
          --table-name "$DYNAMODB_TABLE" \
          --attribute-definitions AttributeName=LockID,AttributeType=S \
          --key-schema AttributeName=LockID,KeyType=HASH \
          --billing-mode PAY_PER_REQUEST \
          --region "$AWS_REGION" > /dev/null 2>&1
        aws dynamodb wait table-exists --table-name "$DYNAMODB_TABLE" --region "$AWS_REGION" 2>&1
        run_terraform destroy
      else
        warn "State backend not found, skipping terraform destroy"
      fi
    fi
  fi

  # Step 2: Clean up resources not managed by Terraform
  cleanup_ecr_repos
  cleanup_memory_resource
  cleanup_agentcore_log_groups
  cleanup_observability_deliveries

  # Step 3: Destroy the state backend itself
  cleanup_state_backend

  info "Full teardown complete"
}

# ---------------------------------------------------------------------------
# Main
