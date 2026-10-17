variable "service_name" { type = string }
variable "alert_email" { type = string }
variable "pubsub_topic_id" { type = string }

locals {
  run_filter = "resource.type=\"cloud_run_revision\" AND resource.label.\"service_name\"=\"${var.service_name}\""
}

resource "google_monitoring_custom_service" "this" {
  service_id   = var.service_name
  display_name = "SentinelRAG API"
}

resource "google_monitoring_slo" "availability" {
  service             = google_monitoring_custom_service.this.service_id
  slo_id              = "availability-99-5"
  display_name        = "99.5% of requests are not 5xx"
  goal                = 0.995
  rolling_period_days = 28

  request_based_sli {
    good_total_ratio {
      bad_service_filter   = "metric.type=\"run.googleapis.com/request_count\" AND ${local.run_filter} AND metric.label.\"response_code_class\"=\"5xx\""
      total_service_filter = "metric.type=\"run.googleapis.com/request_count\" AND ${local.run_filter}"
    }
  }
}

resource "google_monitoring_slo" "latency" {
  service             = google_monitoring_custom_service.this.service_id
  slo_id              = "latency-90pct-under-8s"
  display_name        = "90% of requests under 8s"
  goal                = 0.90
  rolling_period_days = 28

  request_based_sli {
    distribution_cut {
      distribution_filter = "metric.type=\"run.googleapis.com/request_latencies\" AND ${local.run_filter}"
      range {
        min = 0
        max = 8000
      }
    }
  }
}

resource "google_monitoring_notification_channel" "email" {
  display_name = "On-call email"
  type         = "email"
  labels       = { email_address = var.alert_email }
}

resource "google_monitoring_notification_channel" "pubsub" {
  display_name = "Auto-remediation"
  type         = "pubsub"
  labels       = { topic = var.pubsub_topic_id }
}

# Multi-window fast-burn alert (Google SRE Workbook): 5m AND 1h burn rate > 14.4
resource "google_monitoring_alert_policy" "fast_burn" {
  display_name = "${var.service_name} availability fast burn"
  combiner     = "AND"

  conditions {
    display_name = "Burn rate > 14.4 over 5m"
    condition_threshold {
      filter          = "select_slo_burn_rate(\"${google_monitoring_slo.availability.name}\", \"300s\")"
      comparison      = "COMPARISON_GT"
      threshold_value = 14.4
      duration        = "0s"
    }
  }

  conditions {
    display_name = "Burn rate > 14.4 over 1h"
    condition_threshold {
      filter          = "select_slo_burn_rate(\"${google_monitoring_slo.availability.name}\", \"3600s\")"
      comparison      = "COMPARISON_GT"
      threshold_value = 14.4
      duration        = "0s"
    }
  }

  notification_channels = [
    google_monitoring_notification_channel.email.id,
    google_monitoring_notification_channel.pubsub.id,
  ]

  alert_strategy {
    auto_close = "1800s"
  }

  documentation {
    content = "Runbook: docs/incident-runbook.md. The responder service rolls back to the previous healthy revision automatically."
  }
}

# LLM observability from structured logs
resource "google_logging_metric" "tokens" {
  name            = "sentinelrag/llm_tokens"
  filter          = "${local.run_filter} AND jsonPayload.message=\"llm_request\""
  value_extractor = "EXTRACT(jsonPayload.total_tokens)"
  label_extractors = {
    model = "EXTRACT(jsonPayload.model)"
  }
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "DISTRIBUTION"
    unit        = "1"
    labels {
      key        = "model"
      value_type = "STRING"
    }
  }
  bucket_options {
    exponential_buckets {
      num_finite_buckets = 20
      growth_factor      = 2
      scale              = 10
    }
  }
}

resource "google_logging_metric" "cost" {
  name            = "sentinelrag/llm_cost_usd"
  filter          = "${local.run_filter} AND jsonPayload.message=\"llm_request\""
  value_extractor = "EXTRACT(jsonPayload.est_cost_usd)"
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "DISTRIBUTION"
    unit        = "USD"
  }
  bucket_options {
    exponential_buckets {
      num_finite_buckets = 20
      growth_factor      = 2
      scale              = 0.00001
    }
  }
}
