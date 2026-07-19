"""notify_events.py -- publish download events to MQTT and/or a webhook.

A HOOK plugin that fires on download.done / download.failed /
download.needs_review and pushes a small JSON message out -- handy for Home
Assistant (MQTT) or a Discord/Slack/ntfy webhook dashboard.

Two independent, self-gating channels:
  - MQTT   : set MQTT_HOST (uses paho-mqtt if installed; falls back to BD's
             apprise mqtt:// path if available). No host -> no-op.
  - Webhook: set EVENT_WEBHOOK to any URL that accepts a JSON POST. Empty -> no-op.

Configure via environment:
    MQTT_HOST=10.0.70.20   MQTT_PORT=1883   MQTT_TOPIC=bulkdownloader/events
    EVENT_WEBHOOK=https://ntfy.sh/my-topic

Copy into INSTALL_DIR/plugins/ and Reload.
"""
from __future__ import annotations

import json
import os
import urllib.request

PLUGIN = {
    "name": "notify-events",
    "version": "1.0.0",
    "api_version": 2,
    "author": "BulkDownloader (example)",
    "capabilities": ["hook"],
    "description": "Publish download events to MQTT and/or a webhook",
}

from bulk_downloader import plugins as P


def _publish_mqtt(message: dict):
    host = os.environ.get("MQTT_HOST", "").strip()
    if not host:
        return
    topic = os.environ.get("MQTT_TOPIC", "bulkdownloader/events")
    port = int(os.environ.get("MQTT_PORT", "1883") or 1883)
    payload = json.dumps(message)
    try:
        import paho.mqtt.publish as publish  # type: ignore
        publish.single(topic, payload=payload, hostname=host, port=port)
        return
    except Exception:
        pass
    # Fallback: BD's apprise integration supports mqtt:// targets.
    try:
        from bulk_downloader import notify_apprise as _ap  # type: ignore
        if _ap.is_available():
            _ap.send([f"mqtt://{host}:{port}/{topic}"],
                     title="bulkdownloader", body=payload)
    except Exception:
        pass


def _publish_webhook(message: dict):
    url = os.environ.get("EVENT_WEBHOOK", "").strip()
    if not url:
        return
    data = json.dumps(message).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=6.0):
            pass
    except Exception:
        pass


def _emit(event, payload):
    msg = {"event": event,
           "site_id": payload.get("site_id"),
           "filename": payload.get("filename"),
           "file_size": payload.get("file_size"),
           "ts": payload.get("ts")}
    _publish_mqtt(msg)
    _publish_webhook(msg)


@P.hook("download.done")
def on_done(payload):
    _emit("download.done", payload)


@P.hook("download.failed")
def on_failed(payload):
    _emit("download.failed", payload)


@P.hook("download.needs_review")
def on_review(payload):
    _emit("download.needs_review", payload)
