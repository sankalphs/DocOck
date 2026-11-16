"""Shared path parameters.

Every primary key is a Postgres BIGINT. A larger number in a URL used to reach
asyncpg and fail as a 500; bounding it here turns that into a 422 envelope.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Path

MAX_DB_ID = 2**63 - 1

DbId = Annotated[int, Path(ge=1, le=MAX_DB_ID, description="Numeric row id")]

__all__ = ["MAX_DB_ID", "DbId"]
