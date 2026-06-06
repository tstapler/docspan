variable "github_owner" {
  description = "GitHub user or org that owns the repository"
  type        = string
  default     = "tstapler"
}

variable "repository" {
  description = "Repository name (without owner prefix)"
  type        = string
  default     = "docspan"
}

variable "pypi_reviewer_ids" {
  description = "GitHub user IDs required to approve the pypi environment before publish runs. Set to [] to skip the gate."
  type        = list(number)
  default     = []
}
