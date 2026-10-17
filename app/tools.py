"""Tools the agent can call. Gemini decides when to call them (automatic function calling)."""

_STATUS = {
    "checkout-api": "healthy",
    "payments-api": "degraded: elevated 5xx in us-east1",
    "search-api": "healthy",
}


def get_service_status(service_name: str) -> str:
    """Get the current health status of an internal service, e.g. 'payments-api'."""
    return _STATUS.get(service_name, f"unknown service '{service_name}'")


def error_budget_minutes(slo_target_percent: float, window_days: int = 30) -> float:
    """Calculate allowed downtime in minutes for an availability SLO, e.g. 99.9 over 30 days."""
    return round((1 - slo_target_percent / 100) * window_days * 24 * 60, 1)


ALL_TOOLS = [get_service_status, error_budget_minutes]
