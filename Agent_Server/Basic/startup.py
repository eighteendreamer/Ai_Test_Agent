"""
Application startup and lifespan management.
"""

import asyncio
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI

from database.connection import init_db
from Page_Knowledge.runtime_config import load_page_knowledge_runtime_config
from UI_Exploration.service import ensure_ui_exploration_runtime_config


def print_startup_banner():
    """Print startup information using console-safe text."""
    from Basic.config import Config

    print("\n" + "=" * 80)
    print("[Startup] Initializing database...")
    init_db()

    print("\n" + "=" * 80)
    print("[Startup] Loading page knowledge configuration...")
    try:
        cfg = load_page_knowledge_runtime_config()
        print(
            "  [OK] Page Knowledge loaded: "
            f"{cfg.get('collection_name')} dim={cfg.get('vector_size')} "
            f"model={cfg.get('embedding_model')}"
        )
        ensure_ui_exploration_runtime_config()
        print("  [OK] UI_Exploration is using the shared runtime configuration")
    except Exception as exc:
        print(f"  [WARN] Failed to load Page Knowledge configuration: {exc}")

    print("\n" + "=" * 80)
    print("[Startup] Checking LLM model configuration")
    print("=" * 80)

    try:
        from llm import get_active_llm_config

        config = get_active_llm_config()
        print("  [OK] Active model configuration loaded from database")
        print(f"  [OK] Model: {config['model_name']}")
        print(f"  [OK] Provider: {config.get('provider', 'N/A')}")
        print(f"  [OK] Base URL: {config.get('base_url', 'N/A')}")
    except Exception as exc:
        print(f"  [WARN] Unable to load active model configuration: {exc}")
        print("  [WARN] Please add and activate a model in the model management page")

    host = Config.HOST
    port = Config.PORT
    print("\n" + "=" * 80)
    print("[Startup] API documentation endpoints")
    print("=" * 80)
    print(f"  [OK] Swagger UI:   http://{host}:{port}/docs")
    print(f"  [OK] ReDoc:        http://{host}:{port}/redoc")
    print(f"  [OK] OpenAPI JSON: http://{host}:{port}/openapi.json")
    print("=" * 80)

    print("\n[Info] Notes:")
    print("  - The system supports multiple LLM providers.")
    print("  - Configure and switch models from the model management page.")
    print("  - Browser automation can be executed through browser-use.")
    print("=" * 80)

    print("\n[Startup] AI test platform API started successfully.")
    print("=" * 80 + "\n")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage FastAPI startup and shutdown."""
    loop = asyncio.get_running_loop()
    print(f"\n[Debug] Current event loop: {type(loop)}")

    if sys.platform == "win32" and not isinstance(loop, asyncio.ProactorEventLoop):
        print("[Warning] Windows is not using ProactorEventLoop")

    print_startup_banner()

    yield

    print("\n[Shutdown] Service stopped cleanly.\n")
