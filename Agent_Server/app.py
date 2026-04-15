"""
AI 自动化测试平台后端服务入口。
"""

import asyncio
import sys
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Ensure `Agent_Server.*` imports work regardless of the launch directory.
CURRENT_DIR = Path(__file__).resolve().parent
PACKAGE_PARENT = CURRENT_DIR.parent
if str(PACKAGE_PARENT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_PARENT))

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from Agent_Server.Basic.config import config
from Agent_Server.Basic.endpoints import register_basic_endpoints
from Agent_Server.Basic.routes import register_routes
from Agent_Server.Basic.startup import lifespan


app = FastAPI(
    title=config.APP_TITLE,
    description=config.APP_DESCRIPTION,
    version=config.APP_VERSION,
    docs_url="/docs",
    redoc_url=None,
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_routes(app)
register_basic_endpoints(app)


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    uvicorn.run(
        app,
        host=config.HOST,
        port=config.PORT,
        reload=False,
    )
