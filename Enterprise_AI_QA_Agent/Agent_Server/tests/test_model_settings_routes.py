from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from urllib.parse import quote

from src.api.routes.settings import router as settings_router


MODEL_NAME = "Qwen/Qwen3-VL-Embedding-8B"


class _SettingsService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def edit_model_config(self, model_name, payload):
        self.calls.append(("edit", model_name))
        return {"name": model_name, "applications": payload.applications}

    def activate_model_config(self, model_name):
        self.calls.append(("activate", model_name))
        return {"ok": True, "model_name": model_name}

    async def test_model_config_connection(self, model_name):
        self.calls.append(("test", model_name))
        return {"ok": True, "model_name": model_name}

    def delete_model_config(self, model_name):
        self.calls.append(("delete", model_name))
        return {"ok": True, "model_name": model_name}


def _client() -> tuple[TestClient, _SettingsService]:
    app = FastAPI()
    app.include_router(settings_router, prefix="/api/v1")
    service = _SettingsService()
    app.state.settings_service = service
    return TestClient(app), service


def _payload() -> dict:
    return {
        "model_name": MODEL_NAME,
        "provider": "openai",
        "transport": "openai_chat_completions",
        "base_url": "https://api.siliconflow.cn/v1",
        "applications": ["embedding_retrieval"],
    }


def test_model_name_with_slash_is_supported_by_model_settings_routes():
    client, service = _client()
    encoded_model_name = quote(MODEL_NAME, safe="")

    response = client.patch(
        f"/api/v1/settings/models/{encoded_model_name}",
        json=_payload(),
    )
    assert response.status_code == 200
    assert service.calls == [("edit", MODEL_NAME)]

    response = client.post(f"/api/v1/settings/models/{encoded_model_name}/activate")
    assert response.status_code == 200
    response = client.post(f"/api/v1/settings/models/{encoded_model_name}/test-connection")
    assert response.status_code == 200
    response = client.delete(f"/api/v1/settings/models/{encoded_model_name}")
    assert response.status_code == 200
    assert service.calls == [
        ("edit", MODEL_NAME),
        ("activate", MODEL_NAME),
        ("test", MODEL_NAME),
        ("delete", MODEL_NAME),
    ]
