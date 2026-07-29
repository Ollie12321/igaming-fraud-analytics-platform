terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }

  # For a portfolio project a local backend is fine; a team would move this to
  # an S3 backend with a DynamoDB lock table.
  backend "local" {}
}

provider "aws" {
  region  = var.aws_region
  profile = var.aws_profile != "" ? var.aws_profile : null

  default_tags {
    tags = {
      Project     = "igaming-fraud-analytics-platform"
      ManagedBy   = "terraform"
      Environment = var.environment
    }
  }
}
