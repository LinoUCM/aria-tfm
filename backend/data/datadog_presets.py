"""
Datadog alert presets for the simulator.
These replicate real Datadog webhook payloads for demo purposes.

In production, Datadog would send these payloads automatically
when alerts are triggered. For the TFM prototype, we simulate them
to validate the complete workflow without requiring a Datadog account.
"""

from datetime import datetime
import time

DATADOG_PRESETS = {
    "payment_p1_error_rate": {
        "id": "dd-alert-001",
        "title": "[P1] High error rate on payment-service",
        "text": "Error rate exceeded 5% threshold for 5 minutes. Immediate action required.",
        "alert_type": "error",
        "priority": "P1",
        "host": "payment-service-prod-03",
        "tags": ["service:payment-service", "env:production", "team:backend", "region:eu-west-1"],
        "alert_transition": "Triggered",
        "metrics": {
            "error_rate": 12.3,
            "p99_latency_ms": 4823,
            "p50_latency_ms": 890,
            "requests_per_second": 342,
            "error_count": 4207,
        },
        "runbook_url": "https://wiki.company.com/runbooks/payment-service",
        "dashboard_url": "https://app.datadoghq.com/dashboard/payment-overview",
    },

    "auth_p1_latency": {
        "id": "dd-alert-002",
        "title": "[P1] Latency spike on auth-service — p99 > 8s",
        "text": "Authentication service p99 latency has spiked above 8000ms. Users experiencing login failures.",
        "alert_type": "warning",
        "priority": "P1",
        "host": "auth-service-prod-01",
        "tags": ["service:auth-service", "env:production", "team:platform"],
        "alert_transition": "Triggered",
        "metrics": {
            "p99_latency_ms": 8200,
            "p50_latency_ms": 1200,
            "p95_latency_ms": 5400,
            "timeout_rate": 3.4,
            "requests_per_second": 1240,
        },
        "runbook_url": "https://wiki.company.com/runbooks/auth-service",
    },

    "postgres_p2_connections": {
        "id": "dd-alert-003",
        "title": "[P2] PostgreSQL — connection pool near exhaustion",
        "text": "Active connections at 99.6% capacity. New connections being rejected.",
        "alert_type": "error",
        "priority": "P2",
        "host": "postgres-prod-primary",
        "tags": ["service:postgresql", "env:production", "team:data", "db:primary"],
        "alert_transition": "Triggered",
        "metrics": {
            "active_connections": 498,
            "max_connections": 500,
            "waiting_queries": 127,
            "idle_connections": 12,
            "connection_wait_ms": 5400,
        },
        "runbook_url": "https://wiki.company.com/runbooks/postgresql",
    },

    "api_gateway_p2_502": {
        "id": "dd-alert-004",
        "title": "[P2] API Gateway — 502 upstream errors elevated",
        "text": "8.7% of requests returning 502. Upstream services not responding within timeout.",
        "alert_type": "error",
        "priority": "P2",
        "host": "api-gateway-prod",
        "tags": ["service:api-gateway", "env:production", "team:platform"],
        "alert_transition": "Triggered",
        "metrics": {
            "error_rate_502": 8.7,
            "upstream_timeout_ms": 30000,
            "requests_per_second": 2100,
            "healthy_upstreams": 2,
            "total_upstreams": 5,
        },
    },

    "redis_p3_memory": {
        "id": "dd-alert-005",
        "title": "[P3] Redis — memory usage threshold exceeded",
        "text": "Redis memory at 89% capacity. Eviction policy active, potential cache thrashing.",
        "alert_type": "warning",
        "priority": "P3",
        "host": "redis-prod-01",
        "tags": ["service:redis", "env:production", "team:platform"],
        "alert_transition": "Triggered",
        "metrics": {
            "memory_usage_percent": 89.2,
            "used_memory_gb": 13.4,
            "max_memory_gb": 15.0,
            "evicted_keys_per_sec": 234,
            "hit_rate_percent": 94.1,
        },
    },

    "k8s_p1_oom": {
        "id": "dd-alert-006",
        "title": "[P1] Kubernetes — OOMKilled pods in notification-service",
        "text": "3 pods OOMKilled in the last 10 minutes. Service degraded.",
        "alert_type": "error",
        "priority": "P1",
        "host": "k8s-prod-cluster",
        "tags": ["service:notification-service", "env:production", "team:backend", "namespace:default"],
        "alert_transition": "Triggered",
        "metrics": {
            "oomkilled_pods": 3,
            "running_pods": 2,
            "desired_pods": 5,
            "memory_limit_mb": 512,
            "memory_usage_mb": 498,
        },
        "runbook_url": "https://wiki.company.com/runbooks/kubernetes-oom",
    },
}


def get_preset_with_timestamp(preset_key: str) -> dict:
    """Get a preset with current timestamp."""
    if preset_key not in DATADOG_PRESETS:
        raise ValueError(f"Preset '{preset_key}' not found")

    preset = DATADOG_PRESETS[preset_key].copy()
    preset["date_happened"] = int(time.time())
    return preset


def list_presets() -> list[dict]:
    """List all available presets with metadata."""
    return [
        {
            "key": key,
            "title": data["title"],
            "priority": data["priority"],
            "alert_type": data["alert_type"],
            "service": next(
                (t.split("service:")[1] for t in data["tags"] if t.startswith("service:")),
                "unknown"
            ),
        }
        for key, data in DATADOG_PRESETS.items()
    ]
