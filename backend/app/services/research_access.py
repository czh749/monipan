"""A reversible research switch which preserves schema and trading guards."""

import os

from fastapi import HTTPException


def require_research_enabled():
    if os.getenv("MONIPAN_RESEARCH_ENABLED", "1").lower() not in {"1", "true", "yes"}:
        raise HTTPException(503, "池外研究暂时维护中，已有数据保留，请稍后再试")
