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
    null = {
      source  = "hashicorp/null"
      version = "3.2.3"
    }
  }
}
provider "aws" {
  region  = "us-east-1"
  profile = "exam"
}
variable "approved_images" {
  type = string
}
variable "scenario" {
  type    = string
  default = "detect"
}

locals {
  installation_files = [
    "CybereasonSensor-x86_64-${var.scenario}.exe",
    "CybereasonSensor-x86_64-${var.scenario}.deb",
    "CybereasonSensor-arm64-${var.scenario}.deb",
    "dlls.zip",
    "DeveloperCertificates.zip"
  ]
}

module "instance_pipline" {
  depends_on = [null_resource.this]
  source          = "../instance_pipeline"
  name            = "instance_pipeline"
  s3_bucket_name  = aws_s3_bucket.this.bucket
  approved_images = var.approved_images
}
module "ssm_accessibility" {
  depends_on = [null_resource.this]
  source = "../ssm_accessibility"
  name   = "ssm_accessibility_checker"
}
module "sensor_installer" {
  depends_on = [null_resource.this]
  source         = "../sensor_installer"
  name           = "sensor_installer"
  region         = "us-east-1"
  s3_bucket_name = aws_s3_bucket.this.bucket
  scenario       = var.scenario
}

resource "aws_s3_bucket" "this" {
  bucket        = "cybereason-resources-${random_string.this.id}"
  force_destroy = true
}
resource "aws_s3_object" "this" {
  count = length(local.installation_files)
  bucket = aws_s3_bucket.this.bucket
  key    = local.installation_files[count.index]
  source = "./sensor_installer/installation_files/${local.installation_files[count.index]}"
}
resource "null_resource" "this" {
  provisioner "local-exec" {
    command = "./script_zip_files_replace.sh > script_zip_files_replace.log"
  }
}
resource "random_string" "this" {
  length  = 8
  special = false
  upper   = false
}

output "S3-Bucket-Name" {
  value = aws_s3_bucket.this.bucket
}