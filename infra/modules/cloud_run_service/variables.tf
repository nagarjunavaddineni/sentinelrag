variable "name" { type = string }
variable "region" { type = string }
variable "service_account_email" { type = string }

variable "image" {
  type        = string
  default     = "us-docker.pkg.dev/cloudrun/container/hello" # placeholder until CI deploys
  description = "Initial image; CI owns the image afterwards"
}

variable "env" {
  type    = map(string)
  default = {}
}

variable "secret_env" {
  type        = map(string)
  default     = {}
  description = "ENV_NAME => Secret Manager secret_id"
}

variable "max_instances" {
  type    = number
  default = 2
}

variable "allow_unauthenticated" {
  type    = bool
  default = false
}

variable "health_path" {
  type    = string
  default = "/healthz"
}
