"""OAuth 2.0 Authorization Code flow endpoints.

Routes
- GET  /oauth/providers               -> list all supported provider presets
- POST /oauth/start                   -> initiate Auth Code + PKCE flow
- GET  /oauth/callback                -> legacy shared callback URL
- GET  /oauth/{provider}/callback     -> provider-specific callback URL
- GET  /oauth/status/{state}          -> poll for flow completion
"""
from __future__ import annotations

from html import escape

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from src.application.models.oauth_provider_profiles import list_profiles
from src.schemas.oauth import (
    OAuthModelItem,
    OAuthModelsResponse,
    OAuthProviderListResponse,
    OAuthStartRequest,
    OAuthStartResponse,
    OAuthStatusResponse,
)

router = APIRouter(prefix="/oauth", tags=["oauth"])

_PROVIDER_CALLBACK_ALIASES = {
    "azure": "azure_ad",
    "azure_ad": "azure_ad",
    "github": "github",
    "google": "google",
}

# NOTE: This string is returned as-is (no .format()). Use single `{` / `}` in CSS/JS.
# Doubled `{{` would be sent literally and break styles in the browser.
_SUCCESS_HTML = """
<!DOCTYPE html>
<html lang="zh">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>OAuth 授权成功</title>
  <style>
    :root {
      --primary: #111827;
      --primary-hover: #000000;
      --text-base: #1F2937;
      --text-muted: #6B7280;
      --border-color: #E5E7EB;
      --bg-body: #F9FAFB;
      --bg-surface: #FFFFFF;
    }
    * { box-sizing: border-box; -webkit-font-smoothing: antialiased; }
    body {
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      padding: 24px;
      font-family: "Inter", "Segoe UI", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif;
      color: var(--text-base);
      background-color: var(--bg-body);
    }
    .container {
      width: min(100%, 460px);
      padding: 40px;
      background: var(--bg-surface);
      border: 1px solid var(--border-color);
      border-radius: 12px;
      box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05), 0 2px 4px -1px rgba(0, 0, 0, 0.03);
      text-align: center;
    }
    .brand {
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      margin-bottom: 32px;
    }
    .brand-logo {
      font-size: 20px;
    }
    .brand-name {
      font-size: 16px;
      font-weight: 600;
      color: var(--primary);
      letter-spacing: 0.02em;
    }
    .icon-wrapper {
      width: 48px;
      height: 48px;
      margin: 0 auto 20px;
      background: var(--primary);
      color: var(--bg-surface);
      border-radius: 50%;
      display: grid;
      place-items: center;
    }
    .icon-wrapper svg {
      width: 24px;
      height: 24px;
    }
    h1 {
      margin: 0 0 12px;
      font-size: 22px;
      font-weight: 600;
      color: var(--primary);
      letter-spacing: -0.01em;
    }
    p {
      margin: 0 0 24px;
      font-size: 14px;
      line-height: 1.6;
      color: var(--text-muted);
    }
    .countdown {
      display: inline-flex;
      align-items: center;
      padding: 8px 16px;
      background: var(--bg-body);
      border: 1px solid var(--border-color);
      border-radius: 6px;
      font-size: 13px;
      color: var(--text-muted);
      margin-bottom: 32px;
    }
    .countdown strong {
      color: var(--primary);
      font-weight: 600;
      margin: 0 4px;
    }
    .actions {
      display: flex;
      gap: 12px;
      justify-content: center;
    }
    .btn {
      appearance: none;
      border: none;
      padding: 10px 20px;
      font-size: 14px;
      font-weight: 500;
      border-radius: 6px;
      cursor: pointer;
      transition: all 0.2s ease;
    }
    .btn-primary {
      background: var(--primary);
      color: var(--bg-surface);
    }
    .btn-primary:hover {
      background: var(--primary-hover);
    }
    .btn-secondary {
      background: var(--bg-surface);
      color: var(--text-base);
      border: 1px solid var(--border-color);
    }
    .btn-secondary:hover {
      background: var(--bg-body);
    }
    @media (max-width: 480px) {
      .container { padding: 32px 24px; }
      .actions { flex-direction: column; }
      .btn { width: 100%; }
    }
  </style>
</head>
<body>
  <div class="container">
    <div class="brand">
      <span class="brand-logo">🕷</span>
      <span class="brand-name">御策天检</span>
    </div>
    <div class="icon-wrapper">
      <svg fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5">
        <path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7" />
      </svg>
    </div>
    <h1>OAuth 授权成功</h1>
    <p>授权信息已经安全返回系统。你无需手动停留在这个页面，系统会自动带你回到原来的配置界面。</p>
    <div class="countdown">
      将在 <strong id="countdown">3</strong> 秒后自动返回
    </div>
    <div class="actions">
      <button class="btn btn-primary" type="button" onclick="returnToOrigin()">立即返回</button>
      <button class="btn btn-secondary" type="button" onclick="window.close()">关闭页面</button>
    </div>
  </div>
  <script>
    let remaining = 3;
    const countdownEl = document.getElementById("countdown");

    function returnToOrigin() {
      try {
        if (window.opener && !window.opener.closed) {
          window.opener.focus();
        }
      } catch (err) {}

      try {
        window.close();
      } catch (err) {}

      window.location.replace("about:blank");
    }

    const timer = setInterval(() => {
      remaining -= 1;
      if (countdownEl) {
        countdownEl.textContent = String(Math.max(remaining, 0));
      }
      if (remaining <= 0) {
        clearInterval(timer);
        returnToOrigin();
      }
    }, 1000);
  </script>
</body>
</html>
"""

_FAILURE_HTML = """
<!DOCTYPE html>
<html lang="zh">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>OAuth 授权失败</title>
  <style>
    :root {{
      --primary: #111827;
      --text-base: #1F2937;
      --text-muted: #6B7280;
      --border-color: #E5E7EB;
      --bg-body: #F9FAFB;
      --bg-surface: #FFFFFF;
      --error-color: #DC2626;
      --error-bg: #FEF2F2;
    }}
    * {{ box-sizing: border-box; -webkit-font-smoothing: antialiased; }}
    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      padding: 24px;
      font-family: "Inter", "Segoe UI", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif;
      color: var(--text-base);
      background-color: var(--bg-body);
    }}
    .container {{
      width: min(100%, 460px);
      padding: 40px;
      background: var(--bg-surface);
      border: 1px solid var(--border-color);
      border-radius: 12px;
      box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05), 0 2px 4px -1px rgba(0, 0, 0, 0.03);
      text-align: center;
    }}
    .icon-wrapper {{
      width: 48px;
      height: 48px;
      margin: 0 auto 20px;
      background: var(--error-bg);
      color: var(--error-color);
      border-radius: 50%;
      display: grid;
      place-items: center;
    }}
    .icon-wrapper svg {{
      width: 24px;
      height: 24px;
    }}
    h1 {{
      margin: 0 0 12px;
      font-size: 22px;
      font-weight: 600;
      color: var(--primary);
      letter-spacing: -0.01em;
    }}
    p {{
      margin: 0 0 24px;
      font-size: 14px;
      line-height: 1.6;
      color: var(--text-muted);
    }}
    code {{
      display: block;
      margin-top: 16px;
      background: var(--bg-body);
      border: 1px solid var(--border-color);
      padding: 12px 16px;
      border-radius: 6px;
      font-size: 13px;
      color: var(--error-color);
      text-align: left;
      word-break: break-all;
    }}
  </style>
</head>
<body>
  <div class="container">
    <div class="icon-wrapper">
      <svg fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
        <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
      </svg>
    </div>
    <h1>OAuth 授权失败</h1>
    <p>请关闭此标签页，返回配置页面后重新尝试。</p>
    <code>{error}</code>
  </div>
</body>
</html>
"""


def _failure_response(error: str) -> HTMLResponse:
    return HTMLResponse(content=_FAILURE_HTML.format(error=escape(str(error), quote=True)), status_code=200)


@router.get("/providers", response_model=OAuthProviderListResponse)
async def list_oauth_providers():
    """Return all known OAuth provider presets."""
    return OAuthProviderListResponse(providers=list_profiles())


@router.post("/start", response_model=OAuthStartResponse)
async def start_oauth_flow(payload: OAuthStartRequest, request: Request):
    """Initiate an Authorization Code + PKCE flow.

    Returns the authorization URL the frontend should open in a new browser tab.
    Poll ``/oauth/status/{state}`` to detect completion.
    """
    oauth_svc = request.app.state.oauth_token_service
    try:
        return await oauth_svc.start_auth_code_flow(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/callback", response_class=HTMLResponse)
async def oauth_callback(request: Request):
    """Receive the authorization code from the OAuth provider."""
    params = dict(request.query_params)
    code = params.get("code", "").strip()
    state = params.get("state", "").strip()
    error = params.get("error", "").strip()
    error_description = params.get("error_description", "").strip()

    if error:
      detail = error_description or error
      oauth_svc = request.app.state.oauth_token_service
      oauth_svc.mark_failed_flow(
          state,
          f"Provider rejected authorization: {detail}",
      )
      return _failure_response(detail)

    if not code or not state:
      return _failure_response("Missing 'code' or 'state' parameter in callback URL.")

    oauth_svc = request.app.state.oauth_token_service
    await oauth_svc.handle_callback(code=code, state=state)

    result = oauth_svc.get_completed_flow(state)
    if result and result.status == "failed":
      return _failure_response(result.error)

    return HTMLResponse(content=_SUCCESS_HTML, status_code=200)


@router.get("/{provider}/callback", response_class=HTMLResponse)
async def oauth_callback_for_provider(provider: str, request: Request):
    """Receive the authorization code from a provider-specific callback URL."""
    normalized = provider.strip().lower()
    if normalized not in _PROVIDER_CALLBACK_ALIASES:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown OAuth callback provider '{provider}'.",
        )
    return await oauth_callback(request)


@router.get("/models", response_model=OAuthModelsResponse)
async def list_oauth_models(
    request: Request,
    provider: str = Query(..., description="Provider key, e.g. google / github / codex"),
    state: str | None = Query(None, description="State from a completed OAuth flow"),
    base_url: str | None = Query(None, description="Azure resource base URL (azure_ad only)"),
):
    """List models available for an OAuth provider."""
    oauth_svc = request.app.state.oauth_token_service
    try:
        items = await oauth_svc.list_models(provider=provider, state=state, base_url=base_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to fetch models: {exc}") from exc
    return OAuthModelsResponse(
        provider=provider,
        models=[OAuthModelItem(**item) for item in items],
    )


@router.get("/status/{state}", response_model=OAuthStatusResponse)
async def get_oauth_status(state: str, request: Request):
    """Poll for the result of an OAuth flow."""
    oauth_svc = request.app.state.oauth_token_service
    return await oauth_svc.get_flow_status(state)
