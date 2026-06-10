# S3 backend with DynamoDB locking for remote state.
# Values are provided at init time via -backend-config flags or a backend.hcl file.
# Example:
#   terraform init \
#     -backend-config="bucket=my-tf-state-bucket" \
#     -backend-config="dynamodb_table=my-tf-lock-table" \
#     -backend-config="region=us-east-1"
terraform {
  backend "s3" {
    key     = "cloudops-platform/terraform.tfstate"
    encrypt = true
  }
}
