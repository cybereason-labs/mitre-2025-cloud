variable "name" {
  type    = string
}
variable "s3_bucket_name" {
  type = string
}
variable "approved_images" {
  type = string
}

data "aws_iam_policy" "AWSLambdaBasicExecutionRole" {
  arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}
data "aws_caller_identity" "this" {}
data "aws_iam_policy_document" "this" {
  statement {
    actions = [
      "iam:CreateRole",
      "iam:PassRole"
    ]
    resources = ["arn:aws:iam::${data.aws_caller_identity.this.account_id}:role/*-default-role"]
  }
  statement {
    actions = [
      "iam:AttachRolePolicy",
      "iam:PutRolePolicy",
      "iam:CreateInstanceProfile",
      "iam:AddRoleToInstanceProfile",
      "iam:ListInstanceProfilesForRole",
      "ec2:StopInstances",
      "ec2:DescribeIamInstanceProfileAssociations",
      "ec2:DisassociateIamInstanceProfile",
      "ec2:DescribeSecurityGroups",
      "ec2:RevokeSecurityGroupIngress",
      "ec2:RevokeSecurityGroupEgress",
      "ec2:AuthorizeSecurityGroupEgress",
      "ec2:ModifyInstanceAttribute",
      "ec2:CreateTags",
      "ec2:AssociateIamInstanceProfile",
      "ec2:DescribeInstances",
      "ec2:CreateSecurityGroup"
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
  environment {
    variables = {
      S3_BUCKET_NAME = var.s3_bucket_name
      APPROVED_IMAGES = var.approved_images
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
  name          = "${var.name}-rule"
  event_pattern = <<EOF
{
  "source": [
    "aws.ec2"
  ],
  "detail-type": [
    "EC2 Instance State-change Notification"
  ],
  "detail": {
    "state": [
      "running"
    ]
  }
}
EOF
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
