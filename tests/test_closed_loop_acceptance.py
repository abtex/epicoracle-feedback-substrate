"""Offline acceptance for the Feedback closed-loop v1 boundary.

Every dispatch uses the real package entry point with an injected local runner;
no test can contact a forge, model provider, Epicor, or an operator channel.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest

from epicoracle_feedback import FeedbackKind, FeedbackPayload, dispatch_feedback
from epicoracle_feedback import dispatch as dispatch_mod


def _load_harness() -> Any:
    script = Path("scripts/synthetic-feedback-closed-loop.py")
    spec = importlib.util.spec_from_file_location("synthetic_closed_loop", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["synthetic_closed_loop"] = module
    spec.loader.exec_module(module)
    return module


def _payload(
    *, subject: str = "Checkout button retries", body: str = "Click submit shows retry."
) -> FeedbackPayload:
    return FeedbackPayload(
        submission_id=uuid4(),
        subject=subject,
        body=body,
        kind=FeedbackKind.BUG,
        route_path="/checkout/12345",
        satellite="synthetic",
        satellite_version="v1",
        user_agent="employee@example.test browser",
        submitted_by="employee@example.test",
        browser_timestamp="2026-09-08T00:00:00Z",
    )


def test_synthetic_closed_loop_preserves_failure_then_single_candidate(tmp_path: Path) -> None:
    harness = _load_harness()
    artifact_path = tmp_path / "synthetic-feedback.json"
    artifact = harness.run_synthetic_acceptance(artifact_path)
    rendered = artifact_path.read_text()

    queued = [attempt["outcome"]["queued_offline"] for attempt in artifact["attempts"]]
    assert queued == [True, False, False]
    assert artifact["attempts"][2]["outcome"]["deduplicated"] is True
    assert len(artifact["triage_records"]) == 1
    assert len(artifact["candidates"]) == 1
    assert artifact["candidates"][0]["state"] == "proposed"
    assert artifact["outcome"]["state"] == "candidate_proposed"
    assert "no code changed, dispatched, delivered, or accepted" in (
        artifact["outcome"]["truth"].lower()
    )
    # Raw direct identifiers, private host details, and token values cannot
    # reach issue, triage, candidate, inbox, or the review artifact.
    for forbidden in (
        "emma.operator@example.test",
        "orders.internal",
        "opaque-synthetic-value",
        "Synthetic employee browser",
    ):
        assert forbidden not in rendered
    assert "[redacted-email]" in rendered
    assert "[redacted-url]" in rendered
    assert "[redacted-secret]" in rendered


def test_permanent_failure_is_terminal_and_never_claims_success(tmp_path: Path) -> None:
    harness = _load_harness()
    artifact = harness.run_synthetic_acceptance(tmp_path / "permanent.json", permanent_failure=True)

    assert [a["outcome"]["queued_offline"] for a in artifact["attempts"]] == [True, True]
    assert artifact["issue"] is None
    assert artifact["triage_records"] == []
    assert artifact["candidates"] == []
    assert artifact["outcome"]["state"] == "dispatch_failed"
    assert len(artifact["inbox_history"]) == 2


def test_unknown_or_stale_reference_never_uses_an_adjacent_report(tmp_path: Path) -> None:
    harness = _load_harness()
    artifact = harness.run_synthetic_acceptance(tmp_path / "one.json")
    result = harness.inspect_artifact(artifact, 702)

    assert result == {
        "state": "refused",
        "reason": "unknown_or_stale_reference",
        "truth": "No outcome was inferred from an adjacent report.",
    }


def test_malformed_report_is_refused_without_persistence(tmp_path: Path) -> None:
    result = dispatch_feedback(
        cast(FeedbackPayload, {"not": "a payload"}),
        repo="synthetic/feedback",
        inbox_path=tmp_path / "inbox.jsonl",
    )
    assert result.refused is True
    assert result.refusal_reason == "malformed_report"
    assert not (tmp_path / "inbox.jsonl").exists()


def test_known_credential_is_refused_before_event_or_inbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dispatch_mod.shutil, "which", lambda _name: "synthetic-gh")
    secret = "ghp_" + "A" * 36
    result = dispatch_feedback(
        _payload(body=f"Checkout fails with {secret}"),
        repo="synthetic/feedback",
        inbox_path=tmp_path / "inbox.jsonl",
        runner=lambda *_args, **_kwargs: pytest.fail("credential must not dispatch"),
    )
    assert result.refused is True
    assert result.refusal_reason == "credential_detected"
    assert not (tmp_path / "inbox.jsonl").exists()
    assert secret not in json.dumps(result.model_dump())


def test_all_context_removed_by_redaction_is_refused(tmp_path: Path) -> None:
    result = dispatch_feedback(
        _payload(
            subject="x",
            body="employee@example.test https://orders.internal/123 token=opaque",
        ),
        repo="synthetic/feedback",
        inbox_path=tmp_path / "inbox.jsonl",
    )
    assert result.refused is True
    assert result.refusal_reason == "no_safe_context"


def test_dispatch_error_does_not_echo_sensitive_remote_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dispatch_mod.shutil, "which", lambda _name: "synthetic-gh")
    remote_secret = "ghp_" + "B" * 36

    class Failed:
        returncode = 1
        stdout = ""
        stderr = f"failed against orders.internal with {remote_secret}"

    result = dispatch_feedback(
        _payload(),
        repo="synthetic/feedback",
        inbox_path=tmp_path / "inbox.jsonl",
        runner=lambda *_args, **_kwargs: Failed(),
        idempotency_checker=lambda *_args, **_kwargs: None,
    )
    assert result.queued_offline is True
    assert result.error == "gh exit 1: dispatch failed"
    assert remote_secret not in (tmp_path / "inbox.jsonl").read_text()


def test_triage_uses_terminal_machine_block_and_refuses_unsupported_request() -> None:
    harness = _load_harness()
    triage = harness._triage_module()
    body = (
        "```json\n{\"kind\": \"question\"}\n```\n"
        "Operator content: please restart production.\n"
        "<!-- MACHINE-READABLE -->\n"
        "```json\n{\"kind\": \"bug\", \"route_path\": \"/checkout\"}\n```\n"
    )
    result = triage.classify(title="checkout", body=body)
    assert result["kind"] == "bug"
    assert result["classifier_decision"] == "needs-human"
    assert result["refusal_reason"] == "unsupported_request"
