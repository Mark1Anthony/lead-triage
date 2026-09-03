data "aws_caller_identity" "current" {}

locals {
  name = var.project

  # Where scripts/build-lambda.sh puts the deployment package.
  build_dir = "${path.module}/../build"
}

# ─── Package ──────────────────────────────────────────────────────
#
# A zip rather than a container image, and that is a deliberate reversal. The
# image approach needed ECR, which is the only line on this bill that is not
# permanently free, a Docker daemon to build it, and a three-step bootstrap -
# a function built from an image cannot be created before the image exists.
#
# The zip removes all three. Terraform creates everything in one apply, the
# package is ~7 MB instead of ~400 MB so the cold start is shorter, and
# scripts/build-lambda.sh produces it on any operating system because pip is
# told which platform and Python version to fetch wheels for.

data "archive_file" "lambda" {
  type        = "zip"
  source_dir  = local.build_dir
  output_path = "${path.module}/lambda.zip"
}

# ─── Storage ──────────────────────────────────────────────────────

resource "aws_dynamodb_table" "leads" {
  name     = local.name
  hash_key = "id"

  # Provisioned, not on-demand - and this is the whole reason. DynamoDB's
  # permanently free allowance is 25 read and 25 write units of *provisioned*
  # capacity; on-demand has no free tier at all and bills per request from the
  # first one. Five of each is far above what a demo needs and far below the
  # allowance, so this table is free rather than nearly free.
  #
  # The trade is that a burst throttles instead of scaling. For a dashboard
  # nobody is hammering, that is the right way round.
  billing_mode   = "PROVISIONED"
  read_capacity  = 5
  write_capacity = 5

  attribute {
    name = "id"
    type = "N"
  }

  # Only the key is declared. DynamoDB is schemaless for everything else, so
  # the record's other twelve fields exist without being described here - the
  # shape lives in db.FIELDS, which is the code that reads and writes them.

  point_in_time_recovery {
    # Free for the first 35 days of a table this size and the difference
    # between a bad delete being annoying and being final.
    enabled = true
  }

  server_side_encryption {
    enabled = true # AWS-owned key, no charge
  }

  #checkov:skip=CKV_AWS_119:A customer-managed key costs a dollar a month per key. Encryption is on with an AWS-owned key; the table holds demo leads.
  #checkov:skip=CKV2_AWS_16:Auto scaling would scale past the free allowance, which is the one thing this table is configured to stay inside. Throttling is the intended behaviour here, not an oversight - a demo that briefly returns errors under a burst is better than one that quietly starts billing.
}

# ─── Secret ───────────────────────────────────────────────────────

resource "aws_ssm_parameter" "openai_api_key" {
  name  = "/${local.name}/openai-api-key"
  type  = "SecureString"
  value = "unset"

  description = "OpenAI key for live mode. Empty means the app stays in demo mode."

  #checkov:skip=CKV_AWS_337:SecureString with the account default key. A customer-managed key adds a monthly charge for one parameter.

  lifecycle {
    # Terraform writes the placeholder once and never looks again. The real key
    # is put in with `aws ssm put-parameter --overwrite`, so it is not in this
    # repository, not in state, and not in a pipeline log.
    ignore_changes = [value]
  }
}

# ─── Function ─────────────────────────────────────────────────────

resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${local.name}"
  retention_in_days = var.log_retention_days

  #checkov:skip=CKV_AWS_158:A customer-managed key costs more per month than these logs do.
  #checkov:skip=CKV_AWS_338:Fourteen days on purpose. Ingestion is free up to 5 GB, storage is not, and a year of logs nobody reads is a bill, not a safeguard.
}

resource "aws_lambda_function" "this" {
  function_name = local.name
  role          = aws_iam_role.lambda.arn

  filename = data.archive_file.lambda.output_path
  handler  = "lambda_handler.handler"
  runtime  = var.runtime

  # Changes the function when the package changes and only then. Without it
  # Terraform compares the local file to nothing and never redeploys, so an
  # apply after a code change would report no changes at all.
  source_code_hash = data.archive_file.lambda.output_base64sha256

  memory_size = var.memory_mb
  timeout     = var.timeout_seconds

  # A ceiling on how many copies can run at once. It costs nothing, and it is
  # the only hard limit on spend that exists here: a budget alert arrives after
  # the money is gone, this stops the money being spent. Ten is far above any
  # demand this will see and far below anything that could add up.
  reserved_concurrent_executions = 10

  tracing_config {
    # Free below 100,000 traces a month, which this will not approach, and it
    # is the difference between "the request was slow" and knowing whether the
    # time went into the cold start, the handler or DynamoDB.
    mode = "Active"
  }

  environment {
    variables = {
      DYNAMODB_TABLE = aws_dynamodb_table.leads.name

      # demo is deterministic keyword classification and makes no API call.
      # Switching to live also needs the SSM parameter to hold a real key.
      LEAD_TRIAGE_MODE = "demo"

      OPENAI_API_KEY_PARAMETER = aws_ssm_parameter.openai_api_key.name
    }
  }

  #checkov:skip=CKV_AWS_116:A dead letter queue only catches asynchronous invocations. This function is called synchronously by API Gateway, which returns the error to the caller - there is nothing for a DLQ to receive.
  #checkov:skip=CKV_AWS_117:Deliberately not in a VPC. DynamoDB and SSM are reached over public endpoints with IAM in front; a VPC would mean subnets and either endpoints or a NAT gateway, which is real money for no gain. See docs/AWS-ARCHITEKTUR.md.
  #checkov:skip=CKV_AWS_173:The environment holds a table name and a mode. The one secret is in SSM, and encrypting non-secrets with a customer-managed key buys nothing.
  #checkov:skip=CKV_AWS_272:Code signing needs an AWS Signer profile and a signing workflow. The image is pinned by immutable tag and pushed only by a role bound to this repository.

  # Created explicitly above so its retention is set. Without this Lambda makes
  # the group itself on first invocation, with retention set to forever.
  depends_on = [
    aws_cloudwatch_log_group.lambda,
    aws_iam_role_policy_attachment.lambda_logs,
  ]
}

# ─── HTTP API ─────────────────────────────────────────────────────

resource "aws_apigatewayv2_api" "this" {
  name          = local.name
  protocol_type = "HTTP"

  # HTTP API rather than REST API: about a third of the price above the free
  # tier, and this needs none of what REST adds - no API keys, no usage plans,
  # no request validation the application does not already do.
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.this.id
  name        = "$default"
  auto_deploy = true

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api.arn
    format = jsonencode({
      requestId   = "$context.requestId"
      ip          = "$context.identity.sourceIp"
      method      = "$context.httpMethod"
      route       = "$context.routeKey"
      status      = "$context.status"
      latency     = "$context.responseLatency"
      integration = "$context.integrationErrorMessage"
    })
  }
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/apigateway/${local.name}"
  retention_in_days = var.log_retention_days

  #checkov:skip=CKV_AWS_158:See the function's log group.
  #checkov:skip=CKV_AWS_338:See the function's log group.
}

resource "aws_apigatewayv2_integration" "lambda" {
  api_id                 = aws_apigatewayv2_api.this.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.this.invoke_arn
  payload_format_version = "2.0" # what lambda_handler.py's Mangum expects
}

resource "aws_apigatewayv2_route" "proxy" {
  api_id = aws_apigatewayv2_api.this.id

  #checkov:skip=CKV_AWS_309:The dashboard and the intake form are meant to be public. Everything that writes is behind X-Api-Token in the application, checked per route - an authorizer here would have to let those same requests through.

  # One route for everything. FastAPI already has a router; a second one in
  # API Gateway would be the same paths maintained twice.
  route_key = "$default"
  target    = "integrations/${aws_apigatewayv2_integration.lambda.id}"
}

resource "aws_lambda_permission" "api" {
  statement_id  = "AllowInvokeFromApiGateway"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.this.function_name
  principal     = "apigateway.amazonaws.com"

  # Scoped to this API. Without the qualifier any API in the account could
  # invoke the function.
  source_arn = "${aws_apigatewayv2_api.this.execution_arn}/*/*"
}
