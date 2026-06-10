output "parameter_names" {
  description = "Full SSM parameter names keyed by the short config key — used by scripts to verify writes and to discover the path layout."
  value       = { for k, p in aws_ssm_parameter.this : k => p.name }
}

output "prefix" {
  description = "SSM path prefix for this project + environment (everything under here belongs to shared-config)."
  value       = local.prefix
}
