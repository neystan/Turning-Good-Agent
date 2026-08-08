from __future__ import annotations

import base64
import json
from typing import Any

from ...context.session_context import build_session_context, count_message_tokens


def build_command_catalog(runtime: Any) -> dict[str, list[dict[str, object]]]:
    """从当前 Runtime 生成 Web 唯一的 Slash 命令目录。"""
    entries: list[dict[str, object]] = [
        {
            "id": "inspect.context",
            "kind": "inspect",
            "icon": "context",
            "slash": "/context",
            "label": "查看上下文",
            "description": "打开当前会话的结构化上下文",
            "action": "open_context",
        },
        {
            "id": "inspect.tools",
            "kind": "inspect",
            "icon": "tools",
            "slash": "/tools",
            "label": "查看工具调用",
            "description": "打开当前会话的工具调用记录",
            "action": "open_tools",
        },
        {
            "id": "catalog.compact",
            "kind": "action",
            "icon": "compress",
            "slash": "/compress",
            "label": "上下文压缩",
            "description": "压缩当前会话历史并保留真实 token 记录",
            "action": "execute_catalog",
            "catalog_action": "compact",
        },
        {
            "id": "catalog.skill-evolution",
            "kind": "action",
            "icon": "skill_deposit",
            "slash": "/skill-evolution",
            "label": "运行 Skill 演进",
            "description": "审阅已有 Observation 并生成候选 Skill",
            "action": "execute_catalog",
            "catalog_action": "run_skill_evolution",
        },
        {
            "id": "catalog.dream.session",
            "kind": "action",
            "icon": "dream",
            "slash": "/dream",
            "label": "运行 Dream（当前会话）",
            "description": "仅审阅当前会话并更新长期记忆",
            "action": "execute_catalog",
            "catalog_action": "run_dream:session",
        },
        {
            "id": "catalog.dream.global",
            "kind": "action",
            "icon": "dream",
            "slash": "/dream-global",
            "label": "运行 Dream（全局）",
            "description": "审阅所有可用会话并更新长期记忆",
            "action": "execute_catalog",
            "catalog_action": "run_dream:global",
        },
        {
            "id": "catalog.breakbeat.session",
            "kind": "action",
            "icon": "breakbeat",
            "slash": "/breakbeat",
            "label": "运行 Breakbeat（当前会话）",
            "description": "仅从当前会话更新待办事项",
            "action": "execute_catalog",
            "catalog_action": "run_breakbeat:session",
        },
        {
            "id": "catalog.breakbeat.global",
            "kind": "action",
            "icon": "breakbeat",
            "slash": "/breakbeat-global",
            "label": "运行 Breakbeat（全局）",
            "description": "从所有可用会话更新待办事项",
            "action": "execute_catalog",
            "catalog_action": "run_breakbeat:global",
        },
    ]
    for skill in runtime.skills.list_skills():
        entries.append(
            {
                "id": f"skill.{skill.name}",
                "kind": "skill",
                "icon": "skill",
                "slash": f"/{skill.name}",
                "label": skill.name,
                "description": skill.description,
                "action": "insert_text",
                "insert_text": f"请优先参考 Skill「{skill.name}」：",
            }
        )
    for name, status in sorted(runtime.mcp.statuses.items()):
        if not getattr(status, "connected", False):
            continue
        entries.append(
            {
                "id": f"mcp.{name}",
                "kind": "mcp",
                "icon": "mcp",
                "slash": f"/{name}",
                "label": name,
                "description": "查看已连接 MCP Server",
                "action": "insert_text",
                "insert_text": f"请参考 MCP Server「{name}」：",
            }
        )
    return {"entries": entries}


def build_tool_catalog(runtime: Any, active_revision: str) -> dict[str, object]:
    """返回当前注册且可编辑审批状态的 Tool 目录。"""
    connected_servers = {
        name for name, status in runtime.mcp.statuses.items() if getattr(status, "connected", False)
    }
    approval_names = set(runtime.settings.tool_permissions.approval_required_tools)
    visible: list[dict[str, object]] = []
    visible_names: set[str] = set()
    for name in runtime.agent_loop.tools.tool_names:
        source = _tool_source(name, connected_servers)
        if name.startswith("mcp_") and source is None:
            continue
        tool = runtime.agent_loop.tools.get(name)
        required = name in approval_names
        visible_names.add(name)
        visible.append(
            {
                "name": name,
                "description": str(getattr(tool, "description", "")),
                "source": source or {"kind": "core"},
                "approval_required": required,
                "effective_approval": "automatic"
                if required and runtime.settings.tool_permissions.auto_approve_tools
                else "manual"
                if required
                else "not_required",
            }
        )
    return {
        "active_revision": active_revision,
        "tools": visible,
        "unavailable_approval_required": sorted(approval_names - visible_names),
    }


async def build_context_read_model(runtime: Any, session_id: str, active_revision: str) -> dict[str, object] | None:
    from ...context.token_budget import build_context_token_breakdown

    session = await runtime.sessions.store.load_session(session_id)
    if session is None:
        return None
    history = await runtime.sessions.all_messages(session_id)
    context = build_session_context(session, history)
    breakdown = build_context_token_breakdown(
        summary=context.summary,
        history=context.uncompacted_history,
        current_input="",
        output="",
        profile_memory="",
        openai_tools=runtime.agent_loop.tools.openai_tools(),
        include_current_turn=False,
        skills=[],
    )
    breakdown["max_context_tokens"] = runtime.settings.runtime.max_context_tokens
    return {
        "session_id": session_id,
        "summary": context.summary,
        "full_history_count": len(context.full_history),
        "uncompacted_history_count": len(context.uncompacted_history),
        "uncompacted_history_tokens": count_message_tokens(context.uncompacted_history),
        "uncompacted_messages": [_message_view(item) for item in context.uncompacted_history],
        "token_breakdown": breakdown,
        "active_revision": active_revision,
    }


async def page_tool_calls(runtime: Any, session_id: str, limit: int, cursor: str | None) -> dict[str, object] | None:
    if limit < 1 or limit > 100:
        raise ValueError("limit 必须在 1 到 100 之间")
    if await runtime.sessions.store.load_session(session_id) is None:
        return None
    records = sorted(
        await runtime.sessions.store.all_tool_calls(session_id),
        key=lambda item: (item.created_at, item.tool_call_id),
        reverse=True,
    )
    state = _decode_cursor(cursor) if cursor else None
    if state is None:
        boundary = (records[0].created_at, records[0].tool_call_id) if records else None
        before = None
    else:
        boundary = tuple(state["snapshot"])
        before = tuple(state["before"])
    if boundary is not None:
        records = [item for item in records if (item.created_at, item.tool_call_id) <= boundary]
    if before is not None:
        records = [item for item in records if (item.created_at, item.tool_call_id) < before]
    items = records[:limit]
    next_cursor = None
    if len(records) > len(items) and items and boundary is not None:
        next_cursor = _encode_cursor({"snapshot": list(boundary), "before": [items[-1].created_at, items[-1].tool_call_id]})
    return {
        "items": [_tool_call_view(item) for item in items],
        "next_cursor": next_cursor,
        "snapshot": _encode_cursor({"snapshot": list(boundary)}) if boundary is not None else None,
    }


def build_mcp_server_list(runtime: Any) -> dict[str, list[dict[str, object]]]:
    return {"servers": [_mcp_view(runtime, name) for name in sorted(runtime.mcp.statuses)]}


def build_mcp_server_detail(runtime: Any, name: str) -> dict[str, object] | None:
    if name not in runtime.mcp.statuses:
        return None
    view = _mcp_view(runtime, name)
    catalog = runtime.mcp.catalogs.get(name)
    if catalog is not None:
        view["catalog"] = [
            {"kind": capability.kind, "name": capability.name, "description": capability.description}
            for capability in catalog.tools + catalog.resources + catalog.resource_templates + catalog.prompts
        ]
    return view


def _tool_source(name: str, connected_servers: set[str]) -> dict[str, str] | None:
    for server_name in connected_servers:
        if name.startswith(f"mcp_{server_name}_"):
            return {"kind": "mcp", "server_name": server_name}
    return None


def _mcp_view(runtime: Any, name: str) -> dict[str, object]:
    status = runtime.mcp.statuses[name]
    server = runtime.settings.mcp.servers.get(name)
    catalog = runtime.mcp.catalogs.get(name)
    return {
        "name": name,
        "state": status.state,
        "connected": status.connected,
        "error": status.error,
        "transport": server.transport if server is not None else None,
        "catalog_counts": {
            "tools": len(catalog.tools) if catalog else 0,
            "resources": len(catalog.resources) if catalog else 0,
            "resource_templates": len(catalog.resource_templates) if catalog else 0,
            "prompts": len(catalog.prompts) if catalog else 0,
        },
        "enabled_tools": list(server.enabled_tools) if server is not None else [],
    }


def _message_view(item: Any) -> dict[str, object]:
    return {
        "id": item.id,
        "role": item.role,
        "content": item.content,
        "token_count": item.token_count,
        "created_at": item.created_at,
    }


def _tool_call_view(item: Any) -> dict[str, object]:
    return {
        "turn_id": item.turn_id,
        "tool_call_id": item.tool_call_id,
        "tool_name": item.tool_name,
        "args": item.args,
        "content": item.content,
        "error": item.error,
        "duration_ms": item.duration_ms,
        "created_at": item.created_at,
    }


def _encode_cursor(value: dict[str, object]) -> str:
    return base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode()).decode().rstrip("=")


def _decode_cursor(value: str) -> dict[str, list[str]]:
    try:
        padding = "=" * (-len(value) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(value + padding))
        if not isinstance(decoded, dict):
            raise ValueError
        return decoded
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("cursor 无效") from exc
