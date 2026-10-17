# SLO Policy - Acme Platform

The checkout-api availability SLO is 99.9% measured over a rolling 30-day window.

The search-api latency SLO is: 95% of requests complete in under 400 ms.

When more than 50% of the monthly error budget is consumed, feature releases for that service are frozen until the budget recovers. Only reliability fixes may be deployed during a freeze.

Burn-rate alerts: a fast-burn alert pages on-call when the 1-hour burn rate exceeds 14.4. A slow-burn alert opens a ticket when the 6-hour burn rate exceeds 6.
