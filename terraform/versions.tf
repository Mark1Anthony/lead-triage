terraform {
  required_version = ">= 1.9"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.70"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.6"
    }
  }

  # State stays local. This stack has one operator and one environment, and a
  # remote backend would mean an S3 bucket and a lock table that exist only to
  # hold the state of the things they are billed alongside. terraform.tfstate is
  # gitignored; it contains no secret, because the only one is in SSM and
  # Terraform never reads it back.
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      project    = var.project
      managed-by = "terraform"
    }
  }
}
