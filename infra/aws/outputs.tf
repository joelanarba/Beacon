output "ecr_repository_url" {
  value       = aws_ecr_repository.app.repository_url
  description = "ECR repository URI for Beacon app images."
}

output "ecs_cluster_name" {
  value       = aws_ecs_cluster.main.name
  description = "ECS cluster name."
}

output "ecs_service_name" {
  value       = aws_ecs_service.app.name
  description = "ECS service name."
}

output "load_balancer_dns_name" {
  value       = aws_lb.app.dns_name
  description = "Public load balancer DNS name."
}

output "api_url" {
  value       = var.domain_name != "" ? "https://${var.domain_name}" : "http://${aws_lb.app.dns_name}"
  description = "Preferred API URL."
}

output "app_security_group_id" {
  value       = aws_security_group.app.id
  description = "Security group used by ECS tasks."
}

output "private_subnet_ids" {
  value       = [for subnet in values(aws_subnet.private) : subnet.id]
  description = "Private subnet IDs used by ECS tasks."
}

output "app_env_secret_arn" {
  value       = aws_secretsmanager_secret.app_env.arn
  description = "Secrets Manager secret containing runtime environment values."
}
