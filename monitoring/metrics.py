"""Prometheus metric definitions (counters and histograms)."""
from prometheus_client import Counter, Histogram

REQUESTS = Counter("ujuzify_http_requests_total", "HTTP requests", ["method", "path", "status"])
LATENCY = Histogram("ujuzify_http_request_latency_seconds", "HTTP request latency", ["path"],
                    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10))
ANALYSES = Counter("ujuzify_analyses_total", "Skill-gap analyses completed", ["resolved_by"])
RECOMMENDATIONS = Counter("recommendation_requests_total", "Recommendation requests")
RECOMMENDATION_LATENCY = Histogram("recommendation_request_latency_seconds", "Recommendation latency")
FEEDBACK = Counter("recommendation_feedback_total", "Feedback submissions", ["feedback_type"])
ERRORS = Counter("ujuzify_engine_errors_total", "Engine errors", ["code"])
