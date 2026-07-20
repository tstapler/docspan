output "project_id" {
  value       = google_project.docspan.project_id
  description = "Project ID to use for the manual OAuth consent screen + client setup — see README.md"
}

output "next_steps" {
  value = <<-EOT
    Project + APIs are live. Google gives no API/Terraform resource for OAuth consent
    screens or Desktop-app client IDs (confirmed: the one API that used to cover this,
    IAP OAuth Admin API, was shut down 2026-03-19) — finish manually:

      1. https://console.cloud.google.com/apis/credentials/consent?project=${google_project.docspan.project_id}
         → External → app name "docspan" → your email as support/dev contact → Save
         (stays in Testing mode; add your own Google account under Test users)

      2. https://console.cloud.google.com/apis/credentials?project=${google_project.docspan.project_id}
         → Create Credentials → OAuth client ID → Application type: Desktop app → Create
         → Download JSON

      3. uv run docspan auth setup google_docs
         → choose "1) Personal (OAuth)" → point it at the downloaded JSON
  EOT
}
