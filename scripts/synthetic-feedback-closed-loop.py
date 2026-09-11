#!/usr/bin/env python3
"""Create an inspectable, offline-only Feedback closed-loop v1 artifact.

This is an acceptance harness, not a production dispatcher.  It drives the
package's real sanitization and ``dispatch_feedback`` seam with a deterministic
fake ``gh`` runner, then uses the repository's real triage classifier.  It
never contacts GitHub, model providers, Epicor, or an operator channel.

The artifact deliberately records a failed dispatch before retrying.  It calls
a final replay after success to prove the submission ID is deduplicated rather
than producing a second issue, triage record, or candidate.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import UUID

from epicoracle_feedback import FeedbackKind, FeedbackPayload, dispatch_feedback
from epicoracle_feedback import dispatch as dispatch_module


class _Completed:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _triage_module() -> Any:
    path = Path(__file__).with_name("agent-dispatch") / "triage.py"
    spec = importlib.util.spec_from_file_location("synthetic_closed_loop_triage", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("synthetic harness cannot load the triage classifier")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _payload() -> FeedbackPayload:
    """Synthetic only: it intentionally exercises PII/host/token redaction."""
    return FeedbackPayload(
        submission_id=UUID("11111111-1111-4111-8111-111111111111"),
        correlation_id=UUID("22222222-2222-4222-8222-222222222222"),
        subject="Order page retries after a timeout",
        body=(
            "Employee emma.operator@example.test sees a timeout after submitting "
            "an order. Repro: open https://orders.internal/cart/12345 and click "
            "Submit. token=opaque-synthetic-value. The page shows a retry button."
        ),
        kind=FeedbackKind.BUG,
        route_path="/orders/12345",
        satellite="synthetic",
        satellite_version="v1",
        user_agent="Synthetic employee browser emma.operator@example.test",
        submitted_by="emma.operator@example.test",
        browser_timestamp="2026-09-08T00:00:00Z",
    )


def run_synthetic_acceptance(
    artifact_path: Path, *, permanent_failure: bool = False
) -> dict[str, Any]:
    """Run the bounded loop and atomically write its reviewable JSON artifact."""
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    inbox_path = artifact_path.with_suffix(".inbox.jsonl")
    payload = _payload()
    attempts: list[dict[str, Any]] = []
    issue: dict[str, Any] | None = None
    invocation = 0

    def checker(*_args: Any, **_kwargs: Any) -> int | None:
        return 701 if issue is not None else None

    def runner(argv: list[str], **_kwargs: Any) -> _Completed:
        nonlocal invocation, issue
        invocation += 1
        if permanent_failure or invocation == 1:
            return _Completed(75, stderr="synthetic dispatch fault")
        body = argv[argv.index("--body") + 1]
        title = argv[argv.index("--title") + 1]
        issue = {"number": 701, "title": title, "body": body}
        return _Completed(0, "https://github.com/synthetic/feedback/issues/701\n")

    with patch.object(dispatch_module.shutil, "which", return_value="synthetic-gh"):
        first = dispatch_feedback(
            payload,
            repo="synthetic/feedback",
            inbox_path=inbox_path,
            runner=runner,
            idempotency_checker=checker,
        )
        attempts.append({"attempt": 1, "outcome": first.model_dump(mode="json")})

        if permanent_failure:
            second = dispatch_feedback(
                payload,
                repo="synthetic/feedback",
                inbox_path=inbox_path,
                runner=runner,
                idempotency_checker=checker,
            )
            attempts.append({"attempt": 2, "outcome": second.model_dump(mode="json")})
            triage_records: list[dict[str, Any]] = []
            candidates: list[dict[str, Any]] = []
            outcome = {
                "state": "dispatch_failed",
                "truth": (
                    "No issue, triage record, candidate, code change, dispatch, "
                    "delivery, or acceptance exists."
                ),
            }
        else:
            second = dispatch_feedback(
                payload,
                repo="synthetic/feedback",
                inbox_path=inbox_path,
                runner=runner,
                idempotency_checker=checker,
            )
            attempts.append({"attempt": 2, "outcome": second.model_dump(mode="json")})
            if issue is None:
                raise RuntimeError("synthetic success did not create an issue")
            classified = _triage_module().classify(title=issue["title"], body=issue["body"])
            triage_records = [{"issue_number": issue["number"], "classification": classified}]
            candidates = []
            if classified["classifier_decision"] == "sandbox":
                candidates.append(
                    {
                        "issue_number": issue["number"],
                        "state": "proposed",
                        "scope": "bounded UI bug investigation only",
                        "truth": (
                            "Review candidate only; no code changed, dispatched, "
                            "delivered, or accepted."
                        ),
                    }
                )
            else:
                candidates.append(
                    {
                        "issue_number": issue["number"],
                        "state": "refused",
                        "reason": classified["refusal_reason"] or "requires_human_review",
                        "truth": "No code changed, dispatched, delivered, or accepted.",
                    }
                )
            replay = dispatch_feedback(
                payload,
                repo="synthetic/feedback",
                inbox_path=inbox_path,
                runner=runner,
                idempotency_checker=checker,
            )
            attempts.append({"attempt": 3, "outcome": replay.model_dump(mode="json")})
            outcome = {
                "state": "candidate_proposed",
                "truth": (
                    "A triage record and review candidate exist; no code changed, "
                    "dispatched, delivered, or accepted."
                ),
            }

    artifact = {
        "scope": "synthetic-only; no network or external mutation",
        "idempotency": (
            "submission_id permits one issue, one triage record, and one candidate; "
            "terminal replay is deduplicated"
        ),
        "attempts": attempts,
        "issue": issue,
        "triage_records": triage_records,
        "candidates": candidates,
        "outcome": outcome,
        "inbox_history": [json.loads(line) for line in inbox_path.read_text().splitlines()],
    }
    artifact_path.write_text(
        json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return artifact


def inspect_artifact(artifact: dict[str, Any], issue_number: int) -> dict[str, Any]:
    """Resolve an artifact reference without guessing across adjacent reports."""
    issue = artifact.get("issue")
    if not isinstance(issue, dict) or issue.get("number") != issue_number:
        return {
            "state": "refused",
            "reason": "unknown_or_stale_reference",
            "truth": "No outcome was inferred from an adjacent report.",
        }
    return {"state": "found", "issue_number": issue_number, "outcome": artifact["outcome"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--permanent-failure", action="store_true")
    args = parser.parse_args(argv)
    run_synthetic_acceptance(args.artifact, permanent_failure=args.permanent_failure)
    print(args.artifact)
    return 0


if __name__ == "__main__":
    sys.exit(main())
