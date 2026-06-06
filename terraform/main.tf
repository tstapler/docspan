terraform {
  required_version = ">= 1.5"

  required_providers {
    github = {
      source  = "integrations/github"
      version = "~> 6.0"
    }
  }
}

provider "github" {
  owner = var.github_owner
}

resource "github_repository_environment" "testpypi" {
  repository  = var.repository
  environment = "testpypi"
}

resource "github_repository_environment" "pypi" {
  repository  = var.repository
  environment = "pypi"

  reviewers {
    users = var.pypi_reviewer_ids
  }
}
