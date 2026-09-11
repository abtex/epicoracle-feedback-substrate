#!/usr/bin/env python3
"""Triage step — classifies an operator-feedback issue.

Reads ``ISSUE_TITLE``, ``ISSUE_BODY``, ``ISSUE_NUMBER`` from env (NEVER
inline-interpolated by the workflow — see v2 brief's "do not interpolate
${{ }} into run blocks" anti-pattern). Outputs a classification JSON to
stdout and to ``$GITHUB_OUTPUT`` so subsequent steps can route on it.

Classification axes (per v2 brief):

* ``kind`` — bug | suggestion | question — parsed from the labels embedded
  in the issue body's machine-readable JSON block.
* ``touched_surface`` — auth | tenancy | deploy | financial | ui | data |
  unknown — heuristic from operator body + route_path.
* ``security_keywords`` — list of security-relevant terms detected.
* ``classifier_decision`` — sandbox | trinity | answer-draft | needs-human

This is a cheap classifier (regex + keyword); the v2 brief is explicit
that multi-agent trinity dispatch is reserved for ambiguous/architectural
suggestions. Don't fire trinity on every bug.
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any

# Surfaces that always escalate to trinity regardless of kind.
HIGH_RISK_SURFACES = frozenset({"auth", "tenancy", "deploy", "financial"})

# Keywords that flag a touched surface. First-match wins.
SURFACE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "auth": ("login", "logout", "session", "permission", "auth", "principal", "oauth"),
    "tenancy": ("tenant", "namespace", "isolation", "cross-tenant"),
    "deploy": ("deploy", "rollout", "release", "production"),
    "financial": ("price", "pricing", "cost", "margin", "invoice", "ledger", "gl"),
    "ui": ("button", "click", "modal", "page", "render", "display"),
    "data": ("export", "csv", "report", "query", "epicor", "database"),
}

SECURITY_KEYWORDS = (
    "injection",
    "xss",
    "csrf",
    "sql",
    "eval",
    "exfiltrat",
    "leak",
    "privilege",
)

# These are requests this classifier cannot safely turn into an autonomous
# action.  They remain visible to a human as an explicit refusal, not a
# successful-looking candidate.
UNSUPPORTED_REQUESTS = (
    "create a github issue",
    "run this command",
    "execute this",
    "delete ",
    "restart ",
)


def _read_env() -> tuple[str, str, str]:
    title = os.environ.get("ISSUE_TITLE", "")
    body = os.environ.get("ISSUE_BODY", "")
    number = os.environ.get("ISSUE_NUMBER", "")
    return title, body, number


def _extract_kind(body: str) -> str | None:
    """Parse the terminal substrate-owned machine block, never user JSON."""
    matches = re.findall(
        r"<!-- MACHINE-READABLE -->\s*```json\s*(\{.*?\})\s*```",
        body,
        re.DOTALL,
    )
    if not matches:
        return None
    try:
        machine = json.loads(matches[-1])
    except json.JSONDecodeError:
        return None
    kind = machine.get("kind")
    if isinstance(kind, str) and kind in {"bug", "suggestion", "question"}:
        return kind
    return None


def _detect_surface(body: str, route_path: str) -> str:
    """Heuristically map operator body + route to a touched surface."""
    haystack = f"{body}\n{route_path}".lower()
    for surface, kws in SURFACE_KEYWORDS.items():
        if any(kw in haystack for kw in kws):
            return surface
    return "unknown"


def _extract_route(body: str) -> str:
    m = re.search(r"Route: `([^`]+)`", body)
    return m.group(1) if m else ""


def _detect_security_keywords(body: str) -> list[str]:
    lowered = body.lower()
    return [k for k in SECURITY_KEYWORDS if k in lowered]


def classify(*, title: str, body: str) -> dict[str, Any]:
    kind = _extract_kind(body)
    route = _extract_route(body)
    surface = _detect_surface(body, route)
    sec = _detect_security_keywords(body)
    lowered = f"{title}\n{body}".lower()

    refusal_reason: str | None = None
    if kind is None:
        kind = "unknown"
        refusal_reason = "malformed_triage_record"
    elif any(phrase in lowered for phrase in UNSUPPORTED_REQUESTS):
        refusal_reason = "unsupported_request"

    if refusal_reason is not None:
        decision = "needs-human"
    elif kind == "question":
        decision = "answer-draft"
    elif surface in HIGH_RISK_SURFACES or sec or kind == "suggestion":
        decision = "trinity"
    else:
        decision = "sandbox"

    return {
        "kind": kind,
        "touched_surface": surface,
        "security_keywords": sec,
        "classifier_decision": decision,
        "refusal_reason": refusal_reason,
        "title": title[:120],
    }


def _write_output(result: dict[str, Any]) -> None:
    """Write the classification to GITHUB_OUTPUT for downstream steps."""
    out_path = os.environ.get("GITHUB_OUTPUT")
    serialized = json.dumps(result, separators=(",", ":"))
    if out_path:
        with open(out_path, "a", encoding="utf-8") as fh:
            fh.write(f"classification={serialized}\n")
            fh.write(f"classifier_decision={result['classifier_decision']}\n")
            fh.write(f"kind={result['kind']}\n")
    print(serialized)


def main() -> int:
    title, body, number = _read_env()
    if not body:
        print("triage: ISSUE_BODY env empty — nothing to classify", file=sys.stderr)
        return 2

    result = classify(title=title, body=body)
    result["issue_number"] = number
    _write_output(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
