"""`retrieve_similar_incidents`: memory as an agent tool (hypotheses, never evidence)."""

from __future__ import annotations

from pydantic import Field

from app.agent.tools import (
    SERVICE_RE,
    ToolContext,
    ToolDef,
    ToolResult,
    _Args,
    _check_service,
    register,
)
from app.memory.service import hypothesis, memory


class SimilarArgs(_Args):
    service: str | None = Field(
        default=None,
        pattern=SERVICE_RE.pattern,
        description="Lead service to search around; defaults to the top suspect",
    )


async def retrieve_similar_incidents(ctx: ToolContext, args: SimilarArgs) -> ToolResult:
    if not memory.ready:
        return ToolResult(output={"hypotheses": [], "note": memory.reason or "memory disabled"})
    if args.service is not None:
        _check_service(ctx, args.service)
    result = await memory.search_incident(ctx.incident_ref, lead=args.service)
    return ToolResult(
        output={
            "hypotheses": [hypothesis(hit) for hit in result.hits],
            "note": (
                "HYPOTHESES ONLY: past incidents are not evidence for this incident. "
                "Check them with tools; never cite them."
            ),
        },
        codes=[],  # by construction: memory never becomes an evidence row
    )


def register_memory_tool() -> None:
    register(
        ToolDef(
            name="retrieve_similar_incidents",
            description=(
                "Find resolved past incidents similar to this one. Returns hypotheses to "
                "check, not evidence."
            ),
            args=SimilarArgs,
            handler=retrieve_similar_incidents,
        )
    )


__all__ = ["SimilarArgs", "register_memory_tool", "retrieve_similar_incidents"]
