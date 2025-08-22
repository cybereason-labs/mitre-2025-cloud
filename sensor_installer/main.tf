variable "name" {
  type = string
}
variable "s3_bucket_name" {
  type = string
}
variable "region" {
  type = string
}
variable "retry_timeout" {
  type    = number
  default = 300
}
variable "retry_wait_interval" {
  type    = number
  default = 5
}

data "aws_iam_policy" "AWSLambdaBasicExecutionRole" {
  arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}
data "aws_caller_identity" "this" {}
data "aws_iam_policy_document" "this" {
  statement {
    effect = "Allow"
    actions = [
      "ssm:SendCommand",
      "ssm:GetCommandInvocation",
      "ec2:DescribeInstances",
      "ec2:ModifyInstanceAttribute",
      "ec2:DescribeIamInstanceProfileAssociations",
      "ec2:DisassociateIamInstanceProfile",
      "ec2:AssociateIamInstanceProfile",
      "ec2:CreateTags"
    ]
    resources = ["*"]
  }
}

resource "aws_lambda_function" "this" {
  function_name = "${var.name}-function"
  role          = aws_iam_role.this.arn
  timeout       = 900
  memory_size   = 512
  handler       = "main.lambda_handler"
  runtime       = "python3.13"
  filename      = "${path.cwd}/${path.module}/code/code.zip"
  environment {
    variables = {
      REGION              = var.region
      S3_BUCKET_NAME      = var.s3_bucket_name
      RETRY_TIMEOUT       = var.retry_timeout
      RETRY_WAIT_INTERVAL = var.retry_wait_interval
    }
  }
}
resource "aws_iam_role" "this" {
  name = "${var.name}-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      },
    ]
  })
}
resource "aws_iam_policy" "this" {
  name   = "${var.name}-policy"
  policy = data.aws_iam_policy_document.this.json
}
resource "aws_iam_policy_attachment" "this" {
  for_each = {
    "data.aws_iam_policy.AWSLambdaBasicExecutionRole.name" : data.aws_iam_policy.AWSLambdaBasicExecutionRole.arn,
    "aws_iam_policy.this.name" : aws_iam_policy.this.arn
  }
  roles = [aws_iam_role.this.name]
  name       = each.key
  policy_arn = each.value
}
resource "aws_cloudwatch_event_rule" "this" {
  name                = "${var.name}-rule"
  schedule_expression = "rate(3 minutes)"
}
resource "aws_cloudwatch_event_target" "this" {
  arn       = aws_lambda_function.this.arn
  rule      = aws_cloudwatch_event_rule.this.name
  target_id = aws_lambda_function.this.id
}
resource "aws_lambda_permission" "this" {
  action         = "lambda:InvokeFunction"
  function_name  = aws_lambda_function.this.function_name
  principal      = "events.amazonaws.com"
  statement_id   = "invoke-${var.name}"
  source_arn     = aws_cloudwatch_event_rule.this.arn
  source_account = data.aws_caller_identity.this.account_id
}