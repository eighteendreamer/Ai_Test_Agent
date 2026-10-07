"""Record SDK sends without replacing network, responses or production modules."""
import hashlib
import json
import os
from pathlib import Path
import runpy
import sys
from uuid import uuid4

from src.application.model_clients.openai_client import OpenAIChatClient


def install(directory):
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    original = OpenAIChatClient._make_client

    def make_client(self, config, api_key):
        client = original(self, config, api_key)

        async def request_hook(request):
            raw = await request.aread()
            identity = uuid4().hex
            request.extensions["qa_probe_identity"] = identity
            (root / (identity + ".request.json")).write_bytes(raw)
            (root / (identity + ".send.json")).write_text(json.dumps({
                "process_id": os.getpid(), "method": request.method, "size_bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(), "model_key": config.key,
                "network_outcome": "unknown", "request_headers_saved": False,
            }, ensure_ascii=False, indent=2), encoding="utf-8")

        async def response_hook(response):
            identity = response.request.extensions["qa_probe_identity"]
            (root / (identity + ".response.json")).write_text(json.dumps({
                "status_code": response.status_code, "process_id": os.getpid(),
                "body_read_by_probe": False, "stream_completion_verified": False,
            }, ensure_ascii=False, indent=2), encoding="utf-8")

        client._client.event_hooks["request"].append(request_hook)
        client._client.event_hooks["response"].append(response_hook)
        return client

    OpenAIChatClient._make_client = make_client
    return original


if __name__ == "__main__":
    # Test process only; run the actual CLI/API module with its original args.
    install(os.environ["QA_DEFAULT_PROBE_DIR"])
    module = sys.argv[1]
    sys.argv = [module, *sys.argv[2:]]
    runpy.run_module(module, run_name="__main__", alter_sys=True)
