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

variable "runtime" {
  description = <<-EOT
    Lambda runtime. Must match PYTHON_VERSION in scripts/build-lambda.sh: the
    package contains compiled extensions built for one ABI, and a mismatch
    fails at the first invocation with an import error that does not say why.
  EOT
  type        = string
  default     = "python3.11"

  validation {
    condition     = can(regex("^python3[.](11|12|13)$", var.runtime))
    error_message = "Use a Python runtime AWS still supports."
  }
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
  description = <<-EOT
    Request timeout. Also a cost bound: billing is per GB-second, so the
    longest an invocation can run is the most a single request can cost. Demo
    mode classifies in under a millisecond and the slowest real path is a
    DynamoDB scan, so ten seconds is generous. Live mode with an OpenAI call
    would need more.
  EOT
  type        = number
  default     = 10
}

variable "reserved_concurrency" {
  description = <<-EOT
    Concurrent executions reserved for this function, or -1 for none.

    -1 on a new account, and not by preference: AWS keeps a floor of 10
    unreserved executions per account, and a new account's entire quota is 10,
    so any reservation is refused. The account-wide quota caps concurrency
    instead. Raise the quota first, then set this.
  EOT
  type        = number
  default     = -1
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

variable "alert_email" {
  description = <<-EOT
    Where the CloudWatch alarms send mail. Empty disables them entirely.

    Separate from budget_alert_email on purpose: budgets watch money and
    report a day late, these watch requests and report in minutes. They can go
    to the same address, but they answer different questions.
  EOT
  type        = string
  default     = ""
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
