from __future__ import annotations
import json

from src.schemas.session import ExecutionEvent


def format_sse(event: ExecutionEvent) -> str:
    return f"data: {json.dumps(event.model_dump(mode='json'), ensure_ascii=False)}\n\n"

