from ...cli import build_llm
from ...config.settings import Settings
from ...runtime.runtime import AgentRuntime
from .app import create_app


def create_development_app():
    """创建供 Uvicorn 自动重载使用的本机开发应用。"""
    settings = Settings.load()
    runtime = AgentRuntime.create_default(settings, build_llm(settings))
    return create_app(settings, runtime)
