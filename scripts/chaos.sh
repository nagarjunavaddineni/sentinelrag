#!/usr/bin/env bash
# Injects 50% failures, generates traffic, and lets the SLO alert + responder roll it back.
set -euo pipefail
REGION=${REGION:-us-central1}
URL=$(gcloud run services describe sentinelrag-api --region "$REGION" --format='value(status.url)')
KEY=$(gcloud secrets versions access latest --secret=app-api-key)

echo ">> Deploying a bad revision (FAILURE_RATE=0.5)"
gcloud run services update sentinelrag-api --region "$REGION" --update-env-vars FAILURE_RATE=0.5 --quiet
gcloud run services update-traffic sentinelrag-api --region "$REGION" --to-latest --quiet

echo ">> Sending traffic for ~10 minutes (watch Monitoring > SLOs)"
for i in $(seq 1 600); do
  curl -s -o /dev/null -w "%{http_code}\n" -X POST "$URL/ask" \
    -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
    -d '{"question":"What is the checkout-api SLO?"}'
  sleep 1
done | sort | uniq -c

echo ">> Current traffic split:"
gcloud run services describe sentinelrag-api --region "$REGION" --format='yaml(status.traffic)'
