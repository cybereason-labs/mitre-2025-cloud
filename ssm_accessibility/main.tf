variable "name" {
  type    = string
}

data "aws_iam_policy" "AWSLambdaBasicExecutionRole" {
  arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}
data "aws_caller_identity" "this" {}
data "aws_iam_policy_document" "this" {
  statement {
    actions = [
      "ec2:CreateTags",
      "ec2:DescribeInstances",
      "ssm:DescribeInstanceInformation",
    ]
    resources = ["*"]
  }
}

resource "aws_lambda_function" "this" {
  function_name = "${var.name}-function"
  role          = aws_iam_role.this.arn
  timeout       = 600
  memory_size   = 512
  handler       = "main.lambda_handler"
  runtime       = "python3.13"
  filename      = "${path.cwd}/${path.module}/code/code.zip"
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
  schedule_expression = "rate(2 minutes)"
}
resource "aws_cloudwatch_event_target" "this" {
  arn       = aws_lambda_function.this.arn
  rule      = aws_cloudwatch_event_rule.this.name
  target_id = aws_lambda_function.this.id
}
resource "aws_lambda_permission" "this" {
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.this.function_name
  principal     = "events.amazonaws.com"
  statement_id  = "invoke-${var.name}"
  source_arn    = aws_cloudwatch_event_rule.this.arn
  source_account = data.aws_caller_identity.this.account_id
}