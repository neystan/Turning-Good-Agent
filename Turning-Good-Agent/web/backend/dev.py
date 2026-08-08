from ...config.settings import Settings
from ...gateway.host import GatewayHost
from .app import create_app


def create_development_app():
    """创建供 Uvicorn 自动重载使用的 Gateway 内 Web 应用。"""
    settings = Settings.load()
    return create_app(GatewayHost(settings))
