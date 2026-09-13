"""epicoracle_feedback — shared operator-feedback substrate.

Public API. Satellites and the hub import from here; everything else under
``epicoracle_feedback`` is implementation detail and subject to change.

See the repo README for runbook, rollback, and security posture.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

from epicoracle_feedback.auth import resolve_gh_token
from epicoracle_feedback.credentials import scan_for_credentials
from epicoracle_feedback.dispatch import (
    DEFAULT_INBOX_PATH,
    DEFAULT_LABELS,
    GH_TIMEOUT_S,
    dispatch_feedback,
)
from epicoracle_feedback.events import (
    FeedbackEvent,
    emit_feedback_event,
    register_event_sink,
)
from epicoracle_feedback.ghcr import (
    DEFAULT_GHCR_TAG,
    GHCR_PACKAGE_NAME_ENV,
    GHCR_SANDBOX_ENABLED_ENV,
    resolve_ghcr_image,
    sandbox_pull_enabled,
)
from epicoracle_feedback.http_events import (
    HttpEvent,
    emit_http_event,
    register_http_event_sink,
)
from epicoracle_feedback.idempotency import check_idempotency
from epicoracle_feedback.payload import (
    FeedbackDispatchResult,
    FeedbackKind,
    FeedbackPayload,
)
from epicoracle_feedback.sanitization import SanitizationResult, sanitize_feedback

if TYPE_CHECKING:
    from epicoracle_feedback.feedback_router import (
        FeedbackDispatcher,
        FeedbackResponse,
        FeedbackRouterConfig,
        FeedbackStatus,
        FeedbackStatusResolver,
        FeedbackStatusResponse,
        FeedbackSubmission,
        build_feedback_router,
    )

__version__ = "0.2.3"

__all__ = [
    "DEFAULT_GHCR_TAG",
    "DEFAULT_INBOX_PATH",
    "DEFAULT_LABELS",
    "GHCR_PACKAGE_NAME_ENV",
    "GHCR_SANDBOX_ENABLED_ENV",
    "GH_TIMEOUT_S",
    "FeedbackDispatchResult",
    "FeedbackDispatcher",
    "FeedbackEvent",
    "FeedbackKind",
    "FeedbackPayload",
    "FeedbackResponse",
    "FeedbackRouterConfig",
    "FeedbackStatus",
    "FeedbackStatusResolver",
    "FeedbackStatusResponse",
    "FeedbackSubmission",
    "SanitizationResult",
    "HttpEvent",
    "__version__",
    "build_feedback_router",
    "check_idempotency",
    "dispatch_feedback",
    "emit_feedback_event",
    "emit_http_event",
    "register_event_sink",
    "register_http_event_sink",
    "resolve_gh_token",
    "resolve_ghcr_image",
    "sandbox_pull_enabled",
    "sanitize_feedback",
    "scan_for_credentials",
]


_FASTAPI_EXPORTS = frozenset(
    {
        "FeedbackDispatcher",
        "FeedbackResponse",
        "FeedbackRouterConfig",
        "FeedbackStatus",
        "FeedbackStatusResolver",
        "FeedbackStatusResponse",
        "FeedbackSubmission",
        "build_feedback_router",
    }
)


def __getattr__(name: str) -> Any:
    """Load optional FastAPI exports only when an adapter consumer requests them."""
    if name in _FASTAPI_EXPORTS:
        feedback_router = import_module("epicoracle_feedback.feedback_router")
        return getattr(feedback_router, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
