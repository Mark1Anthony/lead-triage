# AWS enforces no spending limit. A budget sends mail; it does not stop
# anything. It is here because the gap between a five dollar month and a
# hundred dollar month is usually noticing in week one - and because everything
# above is meant to sit inside the permanently free tier, so any real spend at
# all is a signal that something is wrong.

resource "aws_budgets_budget" "monthly" {
  count = var.budget_alert_email == "" ? 0 : 1

  name         = "${local.name}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 50
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_alert_email]
  }

  notification {
    # Forecast is the one that can still be acted on; actual has already
    # happened by the time it arrives.
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.budget_alert_email]
  }
}
