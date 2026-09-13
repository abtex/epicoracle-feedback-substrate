"""Optional FastAPI adapter for the shared feedback dispatcher.

Arms configure their own identity, repository, and status resolver; this module
only owns the reusable HTTP validation, sensitive-input refusal, payload
construction, and dispatch-result projection.  It never constructs a GitHub
client or reads credentials.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, ValidationError

from epicoracle_feedback.credentials import scan_for_credentials
from epicoracle_feedback.dispatch import dispatch_feedback
from epicoracle_feedback.payload import FeedbackDispatchResult, FeedbackKind, FeedbackPayload

logger = logging.getLogger(__name__)


class FeedbackSubmission(BaseModel):
    """Operator-supplied fields accepted by the feedback endpoint."""

    submission_id: UUID
    subject: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1, max_length=5000)
    kind: FeedbackKind
    route_path: str = Field(min_length=1, max_length=512)
    user_agent: str = Field(default="", max_length=512)
    browser_timestamp: str = Field(min_length=1, max_length=64)


class FeedbackResponse(BaseModel):
    """Safe operator-facing projection of a dispatch result."""

    captured_at: str
    issue_url: str | None = None
    issue_number: int | None = None
    queued_offline: bool = False
    deduplicated: bool = False


class FeedbackStatus(BaseModel):
    """Arm-owned lifecycle status returned by a status resolver."""

    state: str = Field(min_length=1, max_length=64)
    issue_url: str | None = None
    issue_number: int | None = None


class FeedbackStatusResponse(FeedbackStatus):
    """Status response bound to the submission ID from the request path."""

    submission_id: UUID


class FeedbackDispatcher(Protocol):
    """Configured dispatch transport with the shared dispatcher signature."""

    def __call__(self, payload: FeedbackPayload, *, repo: str) -> FeedbackDispatchResult: ...


FeedbackStatusResolver = Callable[[UUID], FeedbackStatus | None | Awaitable[FeedbackStatus | None]]


@dataclass(frozen=True)
class FeedbackRouterConfig:
    """Explicit arm configuration required to build a feedback router.

    ``dispatch`` is injectable for deterministic ASGI tests.  Production arms
    use the shared ``dispatch_feedback`` default, which retains the existing
    fail-soft queue, sanitization, and idempotency behavior.
    """

    satellite: str
    satellite_version: str
    repository: str
    submitted_by: str = "anonymous-operator"
    dispatch: FeedbackDispatcher = dispatch_feedback

    def __post_init__(self) -> None:
        for field_name in ("satellite", "satellite_version", "repository", "submitted_by"):
            if not getattr(self, field_name).strip():
                raise ValueError(f"{field_name} must not be empty")


def build_feedback_router(
    config: FeedbackRouterConfig,
    status_resolver: FeedbackStatusResolver,
) -> APIRouter:
    """Build configured POST feedback and arm-owned GET status routes.

    Credential patterns in every client-supplied text field are refused before
    the configured dispatcher is called.  Dispatch transport failures remain
    the dispatcher's existing fail-soft result rather than HTTP exceptions.
    A failing or unavailable status resolver returns ``unknown`` so polling
    remains quiet during offline or arm-specific lookup failures.
    """
    if status_resolver is None:
        raise ValueError("status_resolver is required")

    router = APIRouter(tags=["feedback"])

    @router.post(
        "/api/feedback",
        response_model=FeedbackResponse,
        summary="Submit operator feedback",
    )
    def submit_feedback(submission: FeedbackSubmission) -> FeedbackResponse:
        _reject_sensitive_submission(submission)
        payload = FeedbackPayload(
            submission_id=submission.submission_id,
            subject=submission.subject,
            body=submission.body,
            kind=submission.kind,
            route_path=submission.route_path,
            satellite=config.satellite,
            satellite_version=config.satellite_version,
            user_agent=submission.user_agent,
            submitted_by=config.submitted_by,
            browser_timestamp=submission.browser_timestamp,
        )
        result = config.dispatch(payload, repo=config.repository)
        return FeedbackResponse(
            captured_at=result.captured_at,
            issue_url=result.issue_url,
            issue_number=result.issue_number,
            queued_offline=result.queued_offline,
            deduplicated=result.deduplicated,
        )

    @router.get(
        "/api/feedback/status/{submission_id}",
        response_model=FeedbackStatusResponse,
        summary="Poll lifecycle state for one feedback submission",
    )
    async def get_feedback_status(submission_id: UUID) -> FeedbackStatusResponse:
        resolved = await _resolve_status(status_resolver, submission_id)
        return FeedbackStatusResponse(submission_id=submission_id, **resolved.model_dump())

    return router


def _reject_sensitive_submission(submission: FeedbackSubmission) -> None:
    fields = (
        submission.subject,
        submission.body,
        submission.route_path,
        submission.user_agent,
        submission.browser_timestamp,
    )
    findings = [finding for value in fields for finding in scan_for_credentials(value)]
    if not findings:
        return
    logger.info(
        "feedback.credential_scan.rejected submission_id=%s patterns=%d",
        submission.submission_id,
        len(findings),
    )
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Submission appears to contain credentials. Remove secrets and resubmit.",
    )


async def _resolve_status(
    resolver: FeedbackStatusResolver,
    submission_id: UUID,
) -> FeedbackStatus:
    try:
        resolved = resolver(submission_id)
        if inspect.isawaitable(resolved):
            resolved = await resolved
        if resolved is None:
            return FeedbackStatus(state="unknown")
        return FeedbackStatus.model_validate(resolved)
    except (TypeError, ValidationError, ValueError):
        logger.warning("feedback status resolver returned an invalid status; returning unknown")
    except Exception:  # noqa: BLE001 -- arm status lookup must not break polling
        logger.warning("feedback status resolver failed; returning unknown", exc_info=True)
    return FeedbackStatus(state="unknown")


__all__ = [
    "FeedbackDispatcher",
    "FeedbackResponse",
    "FeedbackRouterConfig",
    "FeedbackStatus",
    "FeedbackStatusResolver",
    "FeedbackStatusResponse",
    "FeedbackSubmission",
    "build_feedback_router",
]
