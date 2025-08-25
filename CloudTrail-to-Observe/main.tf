terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "5.81.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "3.6.3"
    }
  }
}
provider "aws" {
  region  = "us-east-1"
  profile = "exam"
}

variable "name" {
  type = string
}
variable "observe-customer-id" {
  type = string
}
variable "observe-extra" {
  type = string
}
variable "observe-token" {
  sensitive = true
  type = string
}
variable "random-id" {
  type = string
}

data "aws_iam_policy_document" "cloudtrail" {
  statement {
    sid    = "AWSCloudTrailAclCheck"
    effect = "Allow"
    principals {
      type = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }
    actions = ["s3:GetBucketAcl"]
    resources = [aws_s3_bucket.cloudtrail.arn]
    condition {
      test     = "StringEquals"
      variable = "aws:SourceArn"
      values = [
        "arn:aws:cloudtrail:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:trail/${var.name}"
      ]
    }
  }
  statement {
    sid    = "AWSCloudTrailWrite"
    effect = "Allow"
    principals {
      type = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }
    actions = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.cloudtrail.arn}/AWSLogs/${data.aws_caller_identity.current.account_id}/*"]
    condition {
      test     = "StringEquals"
      variable = "s3:x-amz-acl"
      values = ["bucket-owner-full-control"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceArn"
      values = [
        "arn:aws:cloudtrail:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:trail/${var.name}"
      ]
    }
  }
}
data "aws_iam_policy" "AWSLambdaBasicExecutionRole" {
  arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}
data "aws_caller_identity" "current" {}
data "aws_region" "current" {}
data "aws_iam_policy_document" "lambda-permissions" {
  statement {
    effect = "Allow"
    actions = [
      "s3:GetObject"
    ]
    resources = ["${aws_s3_bucket.cloudtrail.arn}/*"]
  }
}

# Create Trail
resource "aws_cloudtrail" "cloudtrail" {
  name                          = var.name
  s3_bucket_name                = aws_s3_bucket.cloudtrail.id
  include_global_service_events = true
  is_multi_region_trail         = true
}
resource "aws_s3_bucket" "cloudtrail" {
  bucket        = "${lower(var.name)}-${var.random-id}"
  force_destroy = true
}
resource "aws_s3_bucket_policy" "cloudtrail" {
  bucket = aws_s3_bucket.cloudtrail.id
  policy = data.aws_iam_policy_document.cloudtrail.json
}

# Collect Logs
resource "aws_lambda_function" "lambda" {
  function_name = "${var.name}-function"
  role          = aws_iam_role.lambda.arn
  timeout       = 300
  memory_size   = 512
  handler       = "main.lambda_handler"
  runtime       = "python3.13"
  filename      = "${path.cwd}/${path.module}/code/code.zip"
  layers = [aws_lambda_layer_version.lambda.arn]
  environment {
    variables = {
      CUSTOMER_ID = var.observe-customer-id
      EXTRA       = var.observe-extra
      TOKEN       = var.observe-token
    }
  }
}
resource "aws_iam_role" "lambda" {
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
resource "aws_iam_policy" "lambda" {
  name   = "${var.name}-policy"
  policy = data.aws_iam_policy_document.lambda-permissions.json
}
resource "aws_iam_policy_attachment" "lambda" {
  for_each = {
    "data.aws_iam_policy.AWSLambdaBasicExecutionRole.name" : data.aws_iam_policy.AWSLambdaBasicExecutionRole.arn,
    "aws_iam_policy.this.name" : aws_iam_policy.lambda.arn
  }
  roles = [aws_iam_role.lambda.name]
  name       = each.key
  policy_arn = each.value
}
resource "aws_lambda_permission" "lambda" {
  statement_id   = "invoke-${var.name}"
  action         = "lambda:InvokeFunction"
  function_name  = aws_lambda_function.lambda.function_name
  principal      = "s3.amazonaws.com"
  source_arn     = aws_s3_bucket.cloudtrail.arn
}
resource "aws_s3_bucket_notification" "lambda" {
  depends_on = [aws_lambda_permission.lambda]
  bucket = aws_s3_bucket.cloudtrail.id
  lambda_function {
    lambda_function_arn = aws_lambda_function.lambda.arn
    events = ["s3:ObjectCreated:*"]
  }
}
resource "aws_lambda_layer_version" "lambda" {
  filename   = "${path.cwd}/${path.module}/code/layer.zip"
  layer_name = "${var.name}-requiuests"
  compatible_runtimes = ["python3.13"]
}

# Misc
# resource "random_string" "this" {
#   length  = 8
#   upper   = false
#   special = false
# }