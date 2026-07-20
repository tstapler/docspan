terraform {
  required_version = ">= 1.5"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

provider "google" {
  project = var.project_id
}

resource "google_project" "docspan" {
  name            = var.project_name
  project_id      = var.project_id
  billing_account = var.billing_account
  # No org_id/folder_id — standalone personal project, matching this account's other projects
  # (wedding-toolkit, tyler-personal-projects, etc. are all unparented too).
}

# Docs/Drive/Sheets all run on Google's free consumer quota for a personal-use OAuth client —
# confirmed no billing account is required (wedding-toolkit runs drive+sheets with
# billing_account = null today).
resource "google_project_service" "apis" {
  for_each = toset([
    "docs.googleapis.com",
    "drive.googleapis.com",
    "sheets.googleapis.com",
  ])

  project = google_project.docspan.project_id
  service = each.value

  disable_dependent_services = false
  disable_on_destroy         = false
}
