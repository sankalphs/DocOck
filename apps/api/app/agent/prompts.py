"""Prompts. Kept short: the context and the tool schemas carry the detail."""

from __future__ import annotations

import json

from app.agent.schemas import FAULT_CLASSES, SUMMARY_MAX_CHARS

DIAGNOSIS_SHAPE = {
    "root_cause": {"service": "<service>", "fault_class": "<one of the classes>"},
    "confidence": 0.0,
    "summary": "<= 1200 chars, plain prose",
    "evidence_codes": ["E0001"],
    "affected_services": ["<service>"],
    "recommended_action": {"action_id": "<catalog id>", "target": "<target>"},
    "alternative_hypotheses": [{"service": "<service>", "confidence": 0.0}],
}

SYSTEM_PROMPT = f"""You are DocOck's incident investigator for a microservice shop.
You find the root-cause service of ONE incident and justify it with evidence.

Rules:
- Tools are read-only. Use them to check the leading suspects; do not repeat a call.
- Evidence is identified by codes like E0042. Cite ONLY codes that appear in the
  context or in a tool result for THIS incident. Never invent a code.
- The suspect ranking is a deterministic starting hypothesis, not the answer.
- Items under "similar_past_incidents" are hypotheses to check, NOT evidence. Never
  cite them; there is no field for them.
- Every number in the summary must appear in an evidence row you cite. If unsure,
  leave the number out. No markdown tables.
- A stopped, paused or network-detached container observed by Docker is a direct
  cause. Errors in callers of a failing service are consequences.
- fault_class is one of: {", ".join(FAULT_CLASSES)}.
- recommended_action comes from the action_catalog and must allow its target.
  Use escalate_human with target "incident" when no action fits.
- confidence is your calibrated probability (0-1) that root_cause.service is right.

When you are done, reply with ONLY one JSON object (no prose, no code fence) of
this shape, summary at most {SUMMARY_MAX_CHARS} characters:
{json.dumps(DIAGNOSIS_SHAPE)}"""


def initial_user_message(context_json: str, *, tool_budget: int) -> str:
    return (
        f"Investigate this incident. You have at most {tool_budget} tool calls.\n"
        f"Context (JSON):\n```json\n{context_json}\n```"
    )


FINALISE_MESSAGE = (
    "Stop calling tools now. Reply with ONLY the diagnosis JSON object, citing only "
    "evidence codes you have seen."
)


def repair_message(errors: list[str]) -> str:
    return (
        "Your diagnosis was rejected by the validator:\n- "
        + "\n- ".join(errors[:12])
        + "\nReply with ONLY a corrected diagnosis JSON object. This is the last attempt."
    )


__all__ = [
    "DIAGNOSIS_SHAPE",
    "FINALISE_MESSAGE",
    "SYSTEM_PROMPT",
    "initial_user_message",
    "repair_message",
]
