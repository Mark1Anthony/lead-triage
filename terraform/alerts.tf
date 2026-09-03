# ─── Fast alerting ────────────────────────────────────────────────
#
# The budgets in this account do send mail, but they watch money, and money
# arrives late: AWS Budgets re-evaluate every eight to twelve hours and the
# billing data behind them lags on top of that. A bot would be found out the
# next day.
#
# This watches requests instead. The metric is published within minutes and
# needs no billing run, so the alarm fires while it is still happening rather
# than after it is paid for.

resource "aws_sns_topic" "alerts" {
  count = var.alert_email == "" ? 0 : 1

  name = "${local.name}-alerts"

  #checkov:skip=CKV_AWS_26:A customer-managed key costs about a dollar a month. What travels through here is "this API got a lot of requests" - the alert is not the secret, the account is.
}

resource "aws_sns_topic_subscription" "email" {
  count = var.alert_email == "" ? 0 : 1

  topic_arn = aws_sns_topic.alerts[0].arn
  protocol  = "email"
  endpoint  = var.alert_email

  # AWS sends a confirmation link and the subscription stays "pending" until it
  # is clicked. Terraform cannot do that part, and an unconfirmed subscription
  # delivers nothing - so the alarm is only real once the mail is answered.
}

resource "aws_cloudwatch_metric_alarm" "request_flood" {
  count = var.alert_email == "" ? 0 : 1

  alarm_name  = "${local.name}-request-flood"
  namespace   = "AWS/ApiGateway"
  metric_name = "Count"

  dimensions = {
    ApiId = aws_apigatewayv2_api.this.id
  }

  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  comparison_operator = "GreaterThanThreshold"

  # Requests in five minutes. The gateway throttle allows at most 1500 in that
  # window, and a person clicking around produces a few dozen - so this sits
  # far above any real use and well inside what an attack reaches immediately.
  threshold = 1000

  alarm_description = "More than 1000 requests in five minutes. Expected: a bot. Response: terraform destroy, or disable the stage."

  alarm_actions = [aws_sns_topic.alerts[0].arn]
  ok_actions    = [aws_sns_topic.alerts[0].arn]

  # No traffic means no data points, which is the normal state here. Without
  # this the alarm would sit in INSUFFICIENT_DATA and mail about it.
  treat_missing_data = "notBreaching"
}

# Errors are a different question from load, and worth knowing about separately:
# a flood of 5xx means the function is failing, which no budget would ever show.
resource "aws_cloudwatch_metric_alarm" "server_errors" {
  count = var.alert_email == "" ? 0 : 1

  alarm_name  = "${local.name}-server-errors"
  namespace   = "AWS/Lambda"
  metric_name = "Errors"

  dimensions = {
    FunctionName = aws_lambda_function.this.function_name
  }

  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  comparison_operator = "GreaterThanThreshold"
  threshold           = 10

  alarm_description = "More than 10 Lambda errors in five minutes."

  alarm_actions      = [aws_sns_topic.alerts[0].arn]
  treat_missing_data = "notBreaching"
}
