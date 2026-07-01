variable "project_name" {
  type        = string
  default     = "beacon"
  description = "Short name used for AWS resource names."
}

variable "environment" {
  type        = string
  default     = "staging"
  description = "Deployment environment name."
}

variable "aws_region" {
  type        = string
  default     = "us-east-1"
  description = "AWS region for all resources."
}

variable "app_image" {
  type        = string
  description = "Full container image URI to deploy to ECS."
}

variable "domain_name" {
  type        = string
  default     = ""
  description = "Optional API DNS name, for example api.example.com."
}

variable "route53_zone_id" {
  type        = string
  default     = ""
  description = "Optional Route53 hosted zone ID for domain_name."
}

variable "certificate_arn" {
  type        = string
  default     = ""
  description = "ACM certificate ARN for HTTPS. Required when domain_name is set."
}

variable "secret_key" {
  type        = string
  sensitive   = true
  description = "Strong JWT signing secret for the Beacon API."
}

variable "cors_allowed_origins" {
  type        = string
  default     = ""
  description = "Comma-separated browser origins allowed to call the API."
}

variable "ingest_shared_secret" {
  type        = string
  sensitive   = true
  default     = ""
  description = "Optional shared secret required in X-Beacon-Ingest-Secret."
}

variable "docs_enabled" {
  type        = bool
  default     = false
  description = "Expose FastAPI docs and OpenAPI schema."
}

variable "simulator_enabled" {
  type        = bool
  default     = false
  description = "Expose the local simulator routes."
}

variable "metrics_public" {
  type        = bool
  default     = false
  description = "Expose /metrics without an access token."
}

variable "db_name" {
  type        = string
  default     = "beacon"
  description = "PostgreSQL database name."
}

variable "db_username" {
  type        = string
  default     = "beacon"
  description = "PostgreSQL application username."
}

variable "db_password" {
  type        = string
  sensitive   = true
  description = "PostgreSQL application password."
}

variable "rabbitmq_username" {
  type        = string
  default     = "beacon"
  description = "RabbitMQ application username."
}

variable "rabbitmq_password" {
  type        = string
  sensitive   = true
  description = "RabbitMQ application password."
}

variable "desired_count" {
  type        = number
  default     = 2
  description = "Number of ECS tasks to run."
}

variable "container_cpu" {
  type        = number
  default     = 512
  description = "ECS task CPU units."
}

variable "container_memory" {
  type        = number
  default     = 1024
  description = "ECS task memory in MiB."
}

variable "allowed_ingress_cidr_blocks" {
  type        = list(string)
  default     = ["0.0.0.0/0"]
  description = "CIDR blocks allowed to reach the public load balancer."
}
