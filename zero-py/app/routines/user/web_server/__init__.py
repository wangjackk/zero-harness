"""HTTP + WS 前门 routine 包.

拆分参考:
- ``server.WebServer``  -- Routine 类主体(生命周期 + @request/@subscribe + WS 分发)
- ``app.build_app``      -- FastAPI 路由构造(HTTP + WS 端点)
- ``agents``             -- agent 管理 handler 函数(on_create/list/stop_agent)
- ``_json._Json``        -- JSONResponse + default=str
"""
from .server import WebServer

__all__ = ['WebServer']
