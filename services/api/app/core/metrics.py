from prometheus_client import Counter, Histogram

HTTP_REQUEST_DURATION = Histogram(
    "pinero_http_request_duration_seconds",
    "API request latency by route template.",
    ["method", "route", "status"],
)

PROVIDER_REQUESTS = Counter(
    "pinero_provider_requests_total",
    "Outbound data-provider requests by outcome.",
    ["provider", "dataset", "outcome"],
)

PROVIDER_REQUEST_DURATION = Histogram(
    "pinero_provider_request_duration_seconds",
    "Outbound data-provider latency.",
    ["provider", "dataset"],
)

RATE_LIMITED_REQUESTS = Counter(
    "pinero_rate_limited_requests_total",
    "Requests rejected by rate limiting.",
    ["scope"],
)
