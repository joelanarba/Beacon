"""Prometheus metrics registry.

Process metrics come from the default registry; ``beacon_app_info`` identifies
the ``/metrics`` target, plus the domain collectors below:

- ingestion: ``beacon_incidents_total``, ``beacon_ussd_sessions_total``.
"""

from __future__ import annotations

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    Counter,
    Gauge,
    Histogram,
    Info,
    generate_latest,
)


def _init_app_info() -> None:
    try:
        info = Info("beacon_app", "Beacon application build info")
        info.info({"version": "0.1.0", "component": "beacon"})
    except ValueError:
        # Already registered (module re-imported under --reload or in tests).
        pass


_init_app_info()


# --------------------------------------------------------------------------- #
# Domain collectors
# --------------------------------------------------------------------------- #
# Every incident accepted at ingestion, labelled so the Grafana dashboard can
# break it down by channel / severity / lifecycle status.
incidents_total = Counter(
    "beacon_incidents_total",
    "Incidents recorded, by source channel, severity, and status.",
    ["source_channel", "severity", "status"],
)

# USSD session outcomes power the funnel panel (how many menu walks complete vs.
# get abandoned or time out on the GSM signaling channel).
ussd_sessions_total = Counter(
    "beacon_ussd_sessions_total",
    "USSD sessions by terminal outcome.",
    ["outcome"],
)

# Dispatch: time from report to assignment, the match distance, and a live gauge
# of available responders by discipline.
dispatch_latency_seconds = Histogram(
    "beacon_dispatch_latency_seconds",
    "Seconds from incident report to responder assignment.",
    ["source_channel"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
)

match_radius_meters = Histogram(
    "beacon_match_radius_meters",
    "Distance (metres) to the matched responder.",
    buckets=(250, 500, 1000, 2500, 5000, 10000, 25000),
)

active_responders = Gauge(
    "beacon_active_responders",
    "Currently AVAILABLE responders, by type.",
    ["type"],
)

# Notification: outbound notifications by egress channel + result.
notifications_total = Counter(
    "beacon_notifications_total",
    "Outbound notifications, by egress channel and result.",
    ["egress_channel", "result"],
)


def render_latest() -> tuple[bytes, str]:
    """Return (payload, content_type) for the /metrics response."""
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
