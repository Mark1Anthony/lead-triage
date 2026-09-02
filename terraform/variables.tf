variable "project" {
  description = "Prefix for every resource name."
  type        = string
  default     = "lead-triage"
}

variable "region" {
  description = "AWS region. Frankfurt keeps the data in Germany."
  type        = string
  default     = "eu-central-1"
}

variable "image_tag" {
  description = <<-EOT
    Tag of the image in ECR the function runs. The pipeline sets this to the
    commit it just built, so a deployment names one specific image rather than
    whatever `latest` points at by the time Lambda pulls it.
  EOT
  type        = string
  default     = "latest"
}

variable "memory_mb" {
  description = <<-EOT
    Lambda memory, which also determines CPU - they are the same dial. 512 is
    enough for FastAPI plus Jinja and keeps cold starts near a second; lower
    saves nothing here because the free tier is measured in GB-seconds and this
    has no traffic.
  EOT
  type        = number
  default     = 512

  validation {
    condition     = var.memory_mb >= 128 && var.memory_mb <= 10240
    error_message = "Lambda accepts 128 MB to 10240 MB."
  }
}

variable "timeout_seconds" {
  description = "Request timeout. API Gateway gives up at 30s regardless, so going higher only delays a client that has already left."
  type        = number
  default     = 30
}

variable "log_retention_days" {
  description = "CloudWatch keeps logs forever by default, and bills for it."
  type        = number
  default     = 14
}

variable "monthly_budget_usd" {
  description = "Spend that triggers an alert. AWS enforces no cap, so this warns - it does not stop anything."
  type        = number
  default     = 5
}

variable "budget_alert_email" {
  description = "Where the budget alert goes. Empty disables the budget."
  type        = string
  default     = ""
}

variable "github_repository" {
  description = "owner/repo permitted to deploy through OIDC."
  type        = string
  default     = "Mark1Anthony/lead-triage"
}
