"""``_Json`` ---- ``JSONResponse`` + ``default=str``.

routine result 可能含非 JSON 原生对象(handle / 自定义类),退化成 str
而不是 500.
"""
from __future__ import annotations

import json
from typing import Any

from fastapi.responses import JSONResponse


class _Json(JSONResponse):
    media_type = 'application/json'

    def render(self, content: Any) -> bytes:
        return json.dumps(
            content,
            ensure_ascii=False,
            default=str,
            allow_nan=False,
        ).encode('utf-8')
