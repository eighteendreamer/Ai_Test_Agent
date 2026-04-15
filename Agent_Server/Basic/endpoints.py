"""
基础端点模块
"""
from fastapi import FastAPI
from fastapi.responses import HTMLResponse


def register_basic_endpoints(app: FastAPI):
    """注册基础端点"""
    
    @app.get("/")
    async def root():
        """API 根端点"""
        return {
            "message": "AI Test Agent API is running",
            "version": "2.0.0",
            "docs": "/docs",
            "features": [
                "Multi-LLM support (OpenAI, DeepSeek, Claude, etc.)",
                "Intelligent test case generation",
                "Browser-Use automated testing",
                "AI-powered bug analysis"
            ]
        }

    @app.get("/health")
    async def health_check():
        """健康检查端点"""
        try:
            from llm import get_active_llm_config
            config = get_active_llm_config()
            llm_status = "active"
            llm_model = config.get('model_name', 'unknown')
        except Exception:
            llm_status = "not_configured"
            llm_model = None
        
        return {
            "status": "healthy",
            "service": "AI Test Agent",
            "version": "2.0.0",
            "llm": {
                "status": llm_status,
                "model": llm_model
            }
        }

    @app.get("/redoc", include_in_schema=False)
    async def redoc_html() -> HTMLResponse:
        """ReDoc API 文档页面"""
        return HTMLResponse(
            content="""
            <!DOCTYPE html>
            <html>
                <head>
                    <title>AI Test Agent - API 文档</title>
                    <meta charset="utf-8"/>
                    <meta name="viewport" content="width=device-width, initial-scale=1">
                    <link href="https://fonts.googleapis.com/css?family=Montserrat:300,400,700|Roboto:300,400,700" rel="stylesheet">
                    <style>
                        body {
                            margin: 0;
                            padding: 0;
                        }
                    </style>
                </head>
                <body>
                    <redoc spec-url="/openapi.json"></redoc>
                    <script src="https://cdn.jsdelivr.net/npm/redoc@latest/bundles/redoc.standalone.js"></script>
                </body>
            </html>
            """
        )
