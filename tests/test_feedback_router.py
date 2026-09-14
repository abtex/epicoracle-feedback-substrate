"""Synthetic ASGI tests for the configured feedback router factory."""

from __future__ import annotations

from collections.abc import Callable
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import epicoracle_feedback
from epicoracle_feedback import (
    FeedbackDispatchResult,
    FeedbackPayload,
    FeedbackRouterConfig,
    FeedbackStatus,
    FeedbackStatusResolver,
    build_feedback_router,
)


def _app(
    dispatch: Callable[..., FeedbackDispatchResult],
    resolver: FeedbackStatusResolver,
) -> FastAPI:
    app = FastAPI()
    app.include_router(
        build_feedback_router(
            FeedbackRouterConfig(
                satellite="example-arm",
                satellite_version="1.0.0",
                repository="example/feedback",
                dispatch=dispatch,
            ),
            resolver,
        )
    )
    return app


def _submission() -> dict[str, str]:
    return {
        "submission_id": str(uuid4()),
        "subject": "Sorting feedback",
        "body": "The table should sort by date.",
        "kind": "suggestion",
        "route_path": "/orders",
        "user_agent": "synthetic-test-agent",
        "browser_timestamp": "2026-01-01T00:00:00Z",
    }


def _unknown_status(_submission_id: UUID) -> FeedbackStatus:
    return FeedbackStatus(state="unknown")


def _result(**overrides: object) -> FeedbackDispatchResult:
    values: dict[str, object] = {
        "issue_url": "https://example.test/issues/7",
        "issue_number": 7,
        "queued_offline": False,
        "captured_at": "2026-01-01T00:00:01Z",
    }
    values.update(overrides)
    return FeedbackDispatchResult(**values)  # type: ignore[arg-type]


def test_submit_constructs_configured_payload_and_projects_success() -> None:
    captured: list[tuple[FeedbackPayload, str]] = []

    def dispatch(payload: FeedbackPayload, *, repo: str) -> FeedbackDispatchResult:
        captured.append((payload, repo))
        return _result()

    with TestClient(_app(dispatch, _unknown_status)) as client:
        response = client.post("/api/feedback", json=_submission())

    assert response.status_code == 200
    assert response.json() == {
        "captured_at": "2026-01-01T00:00:01Z",
        "issue_url": "https://example.test/issues/7",
        "issue_number": 7,
        "queued_offline": False,
        "deduplicated": False,
    }
    assert len(captured) == 1
    payload, repository = captured[0]
    assert repository == "example/feedback"
    assert payload.satellite == "example-arm"
    assert payload.satellite_version == "1.0.0"
    assert payload.submitted_by == "anonymous-operator"


def test_invalid_submission_is_rejected_before_dispatch() -> None:
    calls = 0

    def dispatch(_payload: FeedbackPayload, *, repo: str) -> FeedbackDispatchResult:
        nonlocal calls
        calls += 1
        return _result()

    invalid = _submission()
    del invalid["submission_id"]
    with TestClient(_app(dispatch, _unknown_status)) as client:
        response = client.post("/api/feedback", json=invalid)

    assert response.status_code == 422
    assert calls == 0


@pytest.mark.parametrize(
    "field",
    ["subject", "body", "route_path", "user_agent", "browser_timestamp"],
)
def test_sensitive_client_text_is_rejected_before_dispatch(field: str) -> None:
    calls = 0

    def dispatch(_payload: FeedbackPayload, *, repo: str) -> FeedbackDispatchResult:
        nonlocal calls
        calls += 1
        return _result()

    sensitive = _submission()
    sensitive[field] = "Synthetic credential pattern: AKIAIOSFODNN7EXAMPLE"
    with TestClient(_app(dispatch, _unknown_status)) as client:
        response = client.post("/api/feedback", json=sensitive)

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "Submission appears to contain credentials. Remove secrets and resubmit."
    )
    assert calls == 0


def test_fail_soft_unavailable_dispatch_result_is_preserved() -> None:
    def dispatch(_payload: FeedbackPayload, *, repo: str) -> FeedbackDispatchResult:
        return _result(
            issue_url=None,
            issue_number=None,
            queued_offline=True,
            error="synthetic transport unavailable",
        )

    with TestClient(_app(dispatch, _unknown_status)) as client:
        response = client.post("/api/feedback", json=_submission())

    assert response.status_code == 200
    assert response.json()["queued_offline"] is True
    assert response.json()["issue_url"] is None
    assert response.json()["issue_number"] is None


def test_status_resolver_controls_status_response() -> None:
    submission_id = uuid4()

    def dispatch(_payload: FeedbackPayload, *, repo: str) -> FeedbackDispatchResult:
        return _result()

    def resolve_status(requested: UUID) -> FeedbackStatus:
        assert requested == submission_id
        return FeedbackStatus(
            state="processing",
            issue_url="https://example.test/issues/7",
            issue_number=7,
        )

    with TestClient(_app(dispatch, resolve_status)) as client:
        response = client.get(f"/api/feedback/status/{submission_id}")

    assert response.status_code == 200
    assert response.json() == {
        "submission_id": str(submission_id),
        "state": "processing",
        "issue_url": "https://example.test/issues/7",
        "issue_number": 7,
    }


def test_async_status_resolver_controls_status_response() -> None:
    def dispatch(_payload: FeedbackPayload, *, repo: str) -> FeedbackDispatchResult:
        return _result()

    async def resolve_status(_submission_id: UUID) -> FeedbackStatus:
        return FeedbackStatus(state="fix-ready")

    with TestClient(_app(dispatch, resolve_status)) as client:
        response = client.get(f"/api/feedback/status/{uuid4()}")

    assert response.status_code == 200
    assert response.json()["state"] == "fix-ready"


def test_invalid_status_resolver_result_returns_unknown() -> None:
    def dispatch(_payload: FeedbackPayload, *, repo: str) -> FeedbackDispatchResult:
        return _result()

    def invalid_shape(_submission_id: UUID) -> FeedbackStatus:
        return cast(FeedbackStatus, "not a status response")

    with TestClient(_app(dispatch, invalid_shape)) as client:
        response = client.get(f"/api/feedback/status/{uuid4()}")

    assert response.status_code == 200
    assert response.json()["state"] == "unknown"


def test_failing_status_resolver_returns_unknown() -> None:
    def dispatch(_payload: FeedbackPayload, *, repo: str) -> FeedbackDispatchResult:
        return _result()

    def unavailable(_submission_id: UUID) -> FeedbackStatus:
        raise RuntimeError("synthetic status transport unavailable")

    with TestClient(_app(dispatch, unavailable)) as client:
        response = client.get(f"/api/feedback/status/{uuid4()}")

    assert response.status_code == 200
    assert response.json()["state"] == "unknown"


def test_lazy_adapter_import_explains_missing_fastapi(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing_fastapi(_module_name: str) -> None:
        raise ModuleNotFoundError("No module named 'fastapi'", name="fastapi")

    monkeypatch.setattr(epicoracle_feedback, "import_module", missing_fastapi)

    with pytest.raises(ImportError, match=r"epicoracle-feedback\[fastapi\]"):
        _ = epicoracle_feedback.build_feedback_router


def test_lazy_adapter_import_does_not_mask_other_missing_modules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing_dependency(_module_name: str) -> None:
        raise ModuleNotFoundError("No module named 'other_dependency'", name="other_dependency")

    monkeypatch.setattr(epicoracle_feedback, "import_module", missing_dependency)

    with pytest.raises(ModuleNotFoundError, match="other_dependency"):
        _ = epicoracle_feedback.build_feedback_router
