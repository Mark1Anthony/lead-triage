output "url" {
  description = "Public URL of the deployment."
  value       = aws_apigatewayv2_api.this.api_endpoint
}

output "ecr_repository_url" {
  description = "Registry to tag and push images to."
  value       = aws_ecr_repository.this.repository_url
}

output "function_name" {
  description = "Lambda function name, for the pipeline's update call and for `aws logs tail`."
  value       = aws_lambda_function.this.function_name
}

output "table_name" {
  description = "DynamoDB table the leads live in."
  value       = aws_dynamodb_table.leads.name
}

output "cicd_role_arn" {
  description = <<-EOT
    Role the GitHub workflow assumes. Not a secret: it names an identity, it
    does not authenticate as one. That is what the OIDC exchange is for.
  EOT
  value       = aws_iam_role.cicd.arn
}

output "openai_parameter_name" {
  description = "Where to put a real key to switch the deployment to live mode."
  value       = aws_ssm_parameter.openai_api_key.name
}
