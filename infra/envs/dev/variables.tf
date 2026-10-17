variable "project_id" { type = string }
variable "github_repo" { type = string }
variable "alert_email" { type = string }

variable "region" {
  type    = string
  default = "us-central1"
}
