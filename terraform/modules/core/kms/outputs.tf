output "key_arn" {
  description = "ARN of the platform KMS key"
  value       = aws_kms_key.platform.arn
}

output "key_id" {
  description = "ID of the platform KMS key"
  value       = aws_kms_key.platform.key_id
}
