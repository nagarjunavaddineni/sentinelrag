provider "google" {
  project = var.project_id
  region  = var.region
}

data "google_project" "this" {}

resource "google_artifact_registry_repository" "images" {
  #checkov:skip=CKV_GCP_84:Dev env uses Google-managed encryption; CMEK would be enforced in prod via org policy
  location     = var.region
  repository_id = "sentinelrag"
  format        = "DOCKER"
}

# ---------- Service accounts (least privilege) ----------
resource "google_service_account" "api" {
  account_id   = "sentinelrag-api"
  display_name = "SentinelRAG API runtime"
}

resource "google_service_account" "responder" {
  account_id   = "sentinelrag-responder"
  display_name = "Auto-remediation responder"
}

resource "google_service_account" "pubsub_invoker" {
  account_id   = "pubsub-invoker"
  display_name = "Pub/Sub push to responder"
}

resource "google_project_iam_member" "api_roles" {
  for_each = toset(["roles/aiplatform.user", "roles/cloudtrace.agent"])
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.api.email}"
}

resource "google_project_iam_member" "responder_run_developer" {
  project = var.project_id
  role    = "roles/run.developer"
  member  = "serviceAccount:${google_service_account.responder.email}"
}

resource "google_service_account_iam_member" "responder_acts_as_api" {
  service_account_id = google_service_account.api.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.responder.email}"
}

# ---------- API key in Secret Manager ----------
resource "random_password" "api_key" {
  length  = 32
  special = false
}

resource "google_secret_manager_secret" "api_key" {
  secret_id = "app-api-key"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "api_key" {
  secret      = google_secret_manager_secret.api_key.id
  secret_data = random_password.api_key.result
}

resource "google_secret_manager_secret_iam_member" "api_reads_key" {
  secret_id = google_secret_manager_secret.api_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.api.email}"
}

# ---------- Cloud Run services ----------
module "api" {
  source                = "../../modules/cloud_run_service"
  name                  = "sentinelrag-api"
  region                = var.region
  service_account_email = google_service_account.api.email
  allow_unauthenticated = true # protected by X-API-Key
  env = {
    LLM_MODE                  = "real"
    GOOGLE_GENAI_USE_VERTEXAI = "true"
    GOOGLE_CLOUD_PROJECT      = var.project_id
    GOOGLE_CLOUD_LOCATION     = var.region
    ENABLE_TRACING            = "true"
  }
  secret_env = { APP_API_KEY = google_secret_manager_secret.api_key.secret_id }
  depends_on = [google_secret_manager_secret_version.api_key, google_secret_manager_secret_iam_member.api_reads_key]
}

module "responder" {
  source                = "../../modules/cloud_run_service"
  name                  = "sentinelrag-responder"
  region                = var.region
  service_account_email = google_service_account.responder.email
  max_instances         = 1
  env                   = { TARGET_SERVICE = module.api.id }
}

# ---------- Alert -> Pub/Sub -> responder ----------
resource "google_pubsub_topic" "alerts" {
  #checkov:skip=CKV_GCP_83:Topic carries alert metadata only, no sensitive data; Google-managed encryption is sufficient
  name = "slo-alerts"
}

module "monitoring" {
  source          = "../../modules/slo_monitoring"
  service_name    = module.api.name
  alert_email     = var.alert_email
  pubsub_topic_id = google_pubsub_topic.alerts.id
}

resource "google_pubsub_topic_iam_member" "monitoring_can_publish" {
  topic      = google_pubsub_topic.alerts.id
  role       = "roles/pubsub.publisher"
  member     = "serviceAccount:service-${data.google_project.this.number}@gcp-sa-monitoring-notification.iam.gserviceaccount.com"
  depends_on = [module.monitoring]
}

resource "google_cloud_run_v2_service_iam_member" "pubsub_invokes_responder" {
  name     = module.responder.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.pubsub_invoker.email}"
}

resource "google_pubsub_subscription" "alerts_to_responder" {
  name                 = "alerts-to-responder"
  topic                = google_pubsub_topic.alerts.id
  ack_deadline_seconds = 60
  push_config {
    push_endpoint = module.responder.uri
    oidc_token {
      service_account_email = google_service_account.pubsub_invoker.email
    }
  }
}

# ---------- Keyless CI/CD ----------
module "github_wif" {
  source         = "../../modules/github_wif"
  project_id     = var.project_id
  github_repo    = var.github_repo
  deployer_roles = ["roles/run.developer", "roles/artifactregistry.writer"]
}

resource "google_service_account_iam_member" "deployer_acts_as" {
  for_each = {
    api       = google_service_account.api.name
    responder = google_service_account.responder.name
  }
  service_account_id = each.value
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${module.github_wif.service_account_email}"
}
