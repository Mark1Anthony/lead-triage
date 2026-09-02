# ─── The function's role ──────────────────────────────────────────
#
# Scoped to the four things the code actually does: write its logs, read and
# write its own table, and read one parameter. Not a managed policy, because
# the smallest AWS ships for this is AWSLambdaBasicExecutionRole plus a wildcard
# for DynamoDB, and "the table it owns" is a sharper statement than "DynamoDB".

resource "aws_iam_role" "lambda" {
  name = "${local.name}-lambda"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_iam_policy" "lambda_logs" {
  name = "${local.name}-lambda-logs"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
      Resource = "${aws_cloudwatch_log_group.lambda.arn}:*"
      # No CreateLogGroup: Terraform made the group, and a function that can
      # create groups can create them outside its own retention policy.
    }]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_logs" {
  role       = aws_iam_role.lambda.name
  policy_arn = aws_iam_policy.lambda_logs.arn
}

resource "aws_iam_policy" "lambda_data" {
  name = "${local.name}-lambda-data"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
          "dynamodb:DeleteItem",
          "dynamodb:Scan",
          "dynamodb:DescribeTable", # db.init() checks the table is there
        ]
        Resource = aws_dynamodb_table.leads.arn
        # Deliberately absent: CreateTable, DeleteTable. Terraform owns the
        # table; a request handler that could drop it is a request handler that
        # eventually will.
      },
      {
        Effect   = "Allow"
        Action   = "ssm:GetParameter"
        Resource = aws_ssm_parameter.openai_api_key.arn
      },
      {
        Effect = "Allow"
        Action = "kms:Decrypt"
        # A SecureString is encrypted with the account's default SSM key, and
        # reading it without this permission fails at decrypt, not at GetParameter.
        Resource = "arn:aws:kms:${var.region}:${data.aws_caller_identity.current.account_id}:alias/aws/ssm"
      },
    ]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_data" {
  role       = aws_iam_role.lambda.name
  policy_arn = aws_iam_policy.lambda_data.arn
}

# ─── The pipeline's role ──────────────────────────────────────────
#
# GitHub Actions exchanges its own workflow token for AWS credentials. There is
# no access key in the repository and none to rotate - which is the same reason
# the access key configured on this machine had expired and stopped working.

resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]

  # An account can hold exactly one provider per URL. If another stack created
  # it already, import it rather than applying this:
  #   terraform import aws_iam_openid_connect_provider.github <arn>
}

resource "aws_iam_role" "cicd" {
  name = "${local.name}-cicd"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRoleWithWebIdentity"
      Principal = { Federated = aws_iam_openid_connect_provider.github.arn }
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
        }
        StringLike = {
          # Bound to one branch of one repository. A workflow on a fork or a
          # feature branch presents a different subject and STS refuses it -
          # the check is at the identity provider, not in a workflow file that
          # a pull request could edit.
          "token.actions.githubusercontent.com:sub" = "repo:${var.github_repository}:ref:refs/heads/main"
        }
      }
    }]
  })
}

resource "aws_iam_policy" "cicd" {
  name = "${local.name}-cicd"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        # Account-wide by definition: the token is not tied to a repository.
        Action   = "ecr:GetAuthorizationToken"
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:CompleteLayerUpload",
          "ecr:InitiateLayerUpload",
          "ecr:PutImage",
          "ecr:UploadLayerPart",
        ]
        Resource = aws_ecr_repository.this.arn
      },
      {
        Effect = "Allow"
        # Update the code, and nothing else about the function. Changing its
        # role, its environment or its permissions stays with Terraform.
        Action   = "lambda:UpdateFunctionCode"
        Resource = aws_lambda_function.this.arn
      },
    ]
  })
}

resource "aws_iam_role_policy_attachment" "cicd" {
  role       = aws_iam_role.cicd.name
  policy_arn = aws_iam_policy.cicd.arn
}
