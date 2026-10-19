terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.80"
    }
  }

  # State is local until a remote backend is chosen. For anything shared, use
  # an S3 backend in a separate, versioned bucket (Terraform >= 1.10 can lock
  # with `use_lockfile = true`, no DynamoDB table needed). See aws/README.md.
}
