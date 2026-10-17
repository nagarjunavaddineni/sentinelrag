# Incident Runbook: High 5xx Error Rate

Severity: SEV2 if more than 5% of requests fail for 10 minutes; SEV1 if checkout is fully down.

Step 1: Acknowledge the page in PagerDuty within 5 minutes.

Step 2: Check the most recent deployment. If a deploy happened in the last 2 hours, roll back to the previous Cloud Run revision first and investigate after.

Step 3: Post updates in the #incidents Slack channel every 30 minutes.

Step 4: After resolution, write a blameless postmortem within 5 business days.
