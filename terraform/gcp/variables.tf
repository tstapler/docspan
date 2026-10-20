variable "project_id" {
  description = "Globally-unique GCP project ID for docspan's OAuth client"
  type        = string
  default     = "docspan-sync"
}

variable "project_name" {
  description = "Human-readable GCP project display name"
  type        = string
  default     = "docspan"
}

variable "billing_account" {
  description = "Billing account ID to link (optional — Docs/Drive/Sheets run on free consumer quota without one)"
  type        = string
  default     = null
}
