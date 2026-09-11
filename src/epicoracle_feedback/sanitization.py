"""Sanitization boundary for operator feedback.

The dispatcher is the last shared boundary before feedback can enter an inbox,
an issue body, a workflow environment, or an audit sink.  It therefore applies
this policy even when a satellite router already scanned the request.

Known credentials are refused rather than redacted: preserving a possibly
partial secret is worse than preserving no report.  Direct identifiers and
private host details are redacted, while the remaining bounded diagnostic text
is retained for triage.  A report whose useful context is entirely removed is
refused explicitly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from epicoracle_feedback.credentials import scan_for_credentials
from epicoracle_feedback.payload import FeedbackPayload

_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PHONE = re.compile(r"\b(?:\+?\d[\d(). -]{7,}\d)\b")
_URL = re.compile(r"\b(?:https?|wss?)://[^\s`<>]+", re.IGNORECASE)
_PRIVATE_IP = re.compile(
    r"\b(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|"
    r"172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})\b"
)
_PRIVATE_HOST = re.compile(r"\b[a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)*\.(?:local|internal)\b", re.I)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
_NAMED_SECRET = re.compile(
    r"(?i)\b(?:api[_-]?key|token|password|secret|credential)\s*[:=]\s*[^\s,;`]+"
)
_ROUTE_ID = re.compile(r"(?<=/)(?:\d+|[0-9a-f]{8}-[0-9a-f-]{27,})(?=/|$)", re.I)
_MARKERS = re.compile(r"\[(?:redacted-[a-z-]+)\]")


@dataclass(frozen=True)
class SanitizationResult:
    """A sanitized payload or a stable explicit refusal."""

    payload: FeedbackPayload | None
    refusal_reason: str | None = None


def _redact(text: str) -> str:
    text = _URL.sub("[redacted-url]", text)
    text = _PRIVATE_IP.sub("[redacted-private-ip]", text)
    text = _PRIVATE_HOST.sub("[redacted-private-host]", text)
    text = _EMAIL.sub("[redacted-email]", text)
    text = _PHONE.sub("[redacted-phone]", text)
    text = _BEARER.sub("Bearer [redacted-token]", text)
    return _NAMED_SECRET.sub("[redacted-secret]", text)


def _has_useful_context(subject: str, body: str) -> bool:
    """Require bounded non-sensitive diagnostic prose after redaction."""
    remaining = _MARKERS.sub("", f"{subject} {body}")
    return len(re.findall(r"[A-Za-z]", remaining)) >= 8


def sanitize_feedback(payload: FeedbackPayload) -> SanitizationResult:
    """Apply the shared pre-persistence policy without logging raw input.

    ``credential_detected`` and ``no_safe_context`` are stable refusal reasons
    intentionally safe for events, outcome artifacts, and operator responses.
    """
    operator_fields = (
        payload.subject,
        payload.body,
        payload.route_path,
        payload.user_agent,
        payload.submitted_by,
    )
    if any(scan_for_credentials(value) for value in operator_fields):
        return SanitizationResult(payload=None, refusal_reason="credential_detected")

    subject = _redact(payload.subject)
    # Prevent an operator fence from terminating the data block which protects
    # downstream workflow and model parsers from a forged context section.
    body = _redact(payload.body).replace("```", "``\u200b`")
    if not _has_useful_context(subject, body):
        return SanitizationResult(payload=None, refusal_reason="no_safe_context")

    # Neither value is needed by triage and both routinely contain identifying
    # or fingerprinting data.  The role marker preserves only useful context.
    sanitized = payload.model_copy(
        update={
            "subject": subject,
            "body": body,
            "route_path": _ROUTE_ID.sub("{id}", _redact(payload.route_path)),
            "user_agent": "",
            "submitted_by": "operator",
        }
    )
    return SanitizationResult(payload=sanitized)


__all__ = ["SanitizationResult", "sanitize_feedback"]
