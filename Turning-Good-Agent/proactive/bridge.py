from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from .store import ProactiveStore


_LOOPBACK_HOST = "127.0.0.1"
_PORT_START = 41000
_PORT_COUNT = 2000
_MAX_MESSAGE_BYTES = 1_048_576


def proactive_bridge_port(data_dir: Path) -> int:
    digest = hashlib.sha256(str(data_dir.resolve()).encode("utf-8")).digest()
    return _PORT_START + int.from_bytes(digest[:2], "big") % _PORT_COUNT


class ProactiveBridgePublisher:
    """通过仅限 loopback 的短连接向 Web Host 发送主动事件。"""

    def __init__(self, data_dir: Path) -> None:
        self._port = proactive_bridge_port(data_dir)

    async def publish(self, payload: dict[str, object]) -> None:
        reader, writer = await asyncio.open_connection(_LOOPBACK_HOST, self._port)
        try:
            writer.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n")
            await writer.drain()
            response = await reader.readline()
            if response != b"ok\n":
                raise ValueError("proactive bridge 拒绝事件")
        finally:
            writer.close()
            await writer.wait_closed()


class ProactiveBridgeListener:
    """Web Host 的本机 bridge 接收器；不注册任何 HTTP 或公开 WebSocket 路由。"""

    def __init__(
        self,
        data_dir: Path,
        receiver: Callable[[dict[str, object]], Awaitable[None]],
    ) -> None:
        self._port = proactive_bridge_port(data_dir)
        self._receiver = receiver
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        if self._server is None:
            self._server = await asyncio.start_server(self._handle, _LOOPBACK_HOST, self._port)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        try:
            if not isinstance(peer, tuple) or peer[0] not in {_LOOPBACK_HOST, "::1"}:
                raise ValueError("bridge 仅接受 loopback 连接")
            line = await reader.readline()
            if not line or len(line) > _MAX_MESSAGE_BYTES:
                raise ValueError("bridge payload 无效")
            payload: Any = json.loads(line)
            if not isinstance(payload, dict):
                raise ValueError("bridge payload 必须是对象")
            await self._receiver(payload)
            writer.write(b"ok\n")
        except (OSError, ValueError, json.JSONDecodeError):
            writer.write(b"error\n")
        finally:
            await writer.drain()
            writer.close()
            await writer.wait_closed()


class CliProactiveBridge:
    """将 CLI 所有者的完整主动读模型投递给本机 Web Host。"""

    def __init__(
        self,
        runtime: Any,
        ownership: Callable[[], Any],
        service: Callable[[], Any | None],
    ) -> None:
        self._runtime = runtime
        self._ownership = ownership
        self._service = service
        self._store = ProactiveStore(runtime.settings.data_dir)
        self._publisher = ProactiveBridgePublisher(runtime.settings.data_dir)
        self._revision = 0
        self._pending_snapshots: dict[str, dict[str, object]] = {}

    async def publish_snapshot(self, domain: str) -> None:
        payload = self._snapshot_payload(domain)
        payload["type"] = "snapshot"
        await self._send(payload, snapshot_domain=domain)

    async def retry_snapshots(self) -> None:
        for domain, payload in tuple(self._pending_snapshots.items()):
            if await self._deliver(payload):
                self._pending_snapshots.pop(domain, None)

    async def publish_notice(self, *, event_type: str, content: str, source_id: str) -> None:
        domain = _notice_domain(event_type)
        payload = self._snapshot_payload(domain)
        payload.update(
            {
                "type": "notice",
                "id": str(uuid4()),
                "entity_id": source_id,
                "severity": "error" if event_type == "proactive.incident.opened" else "info",
                "title": _notice_title(event_type),
                "message": content,
                "target": _notice_target(domain),
            }
        )
        await self._send(payload)

    async def _send(self, payload: dict[str, object], *, snapshot_domain: str | None = None) -> None:
        owner = self._ownership()
        if not bool(getattr(owner, "writable", False)) or getattr(owner, "owner_kind", None) != "cli":
            self._pending_snapshots.clear()
            return
        self._revision += 1
        payload["proactive_revision"] = self._revision
        payload["owner"] = owner.to_dict()
        if await self._deliver(payload):
            if snapshot_domain is not None:
                self._pending_snapshots.pop(snapshot_domain, None)
        elif snapshot_domain is not None:
            self._pending_snapshots[snapshot_domain] = payload

    async def _deliver(self, payload: dict[str, object]) -> bool:
        try:
            await self._publisher.publish(payload)
        except (OSError, ValueError):
            return False
        return True

    def _snapshot_payload(self, domain: str) -> dict[str, object]:
        return {
            "domain": domain,
            "data": self._domain_data(domain),
            "runtime": self._runtime_data(domain),
        }

    def _domain_data(self, domain: str) -> dict[str, object]:
        defaults = {
            "cron": {"jobs": [], "usage": _usage()},
            "breakbeat": {"items": [], "cursors": {}, "next_run_at": None, "usage": _usage()},
            "dream": {"cursors": {}, "next_run_at": None, "usage": _usage()},
            "skill": {"observations": [], "next_run_at": None, "usage": _usage()},
            "incident": {"incidents": []},
        }
        names = {
            "cron": "cron.json",
            "breakbeat": "breakbeat.json",
            "dream": "dream.json",
            "skill": "skill.json",
            "incident": "incidents.json",
        }
        data = self._store.read_json(names[domain], defaults[domain])
        if not isinstance(data, dict):
            raise ValueError("proactive bridge snapshot 必须是对象")
        result: dict[str, object] = dict(data)
        if domain == "dream":
            memory = self._runtime.profile_memory.read()
            result["memory"] = {"user": memory.user, "soul": memory.soul}
        if domain == "skill":
            result["drafts"] = _drafts(self._runtime)
        return result

    def _runtime_data(self, domain: str) -> dict[str, object]:
        service = self._service()
        names = {
            "cron": "cron",
            "breakbeat": "breakbeat",
            "dream": "dream",
            "skill": "skill_evolution",
            "incident": "incidents",
        }
        manager = getattr(service, names[domain], None) if service is not None else None
        if domain == "cron" and manager is not None:
            data = self._domain_data("cron")
            states = {
                str(job["id"]): str(manager.runtime_state(str(job["id"])))
                for job in data.get("jobs", [])
                if isinstance(job, dict) and isinstance(job.get("id"), str)
            }
            deadline = manager.next_deadline()
            return {
                "running": any(state in {"queued", "running"} for state in states.values()),
                "next_run_at": _iso_value(deadline),
                "entity_states": states,
            }
        running = bool(getattr(manager, "running", False))
        return {
            "running": running,
            "next_run_at": _iso_value(getattr(manager, "next_run_at", None)),
            "entity_states": {"service": "running" if running else "idle"},
        }


class CliBridgeDeliverySink:
    """保留 CLI durable 投递，同时镜像有限的 Web 通知。"""

    def __init__(self, delegate: Any, bridge: CliProactiveBridge) -> None:
        self._delegate = delegate
        self._bridge = bridge

    async def enqueue(self, **payload: str) -> dict[str, str]:
        result = await self._delegate.enqueue(**payload)
        await self._bridge.publish_notice(
            event_type=payload["event_type"],
            content=payload["content"],
            source_id=payload["source_id"],
        )
        return result

    async def flush(self) -> int:
        return await self._delegate.flush()

    async def delete_by_source_id(self, source_id: str) -> int:
        return await self._delegate.delete_by_source_id(source_id)


def _usage() -> dict[str, int]:
    return {"calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


def _iso_value(value: object) -> str | None:
    isoformat = getattr(value, "isoformat", None)
    return isoformat() if callable(isoformat) else None


def _drafts(runtime: Any) -> list[dict[str, str]]:
    skills = runtime.skills
    root = skills.directory / ".drafts"
    result = []
    for name in skills.list_drafts():
        try:
            manifest = skills.validator.validate(root / name)
        except (AttributeError, OSError, ValueError):
            continue
        result.append({"name": manifest.name, "description": manifest.description, "body": manifest.body})
    return result


def _notice_domain(event_type: str) -> str:
    if ".cron." in event_type:
        return "cron"
    if ".breakbeat." in event_type:
        return "breakbeat"
    if ".dream." in event_type:
        return "dream"
    if ".skill_evolution." in event_type:
        return "skill"
    return "incident"


def _notice_target(domain: str) -> str:
    return {
        "cron": "#proactive/cron",
        "breakbeat": "#proactive/breakbeat",
        "dream": "#proactive/memory",
        "skill": "#proactive/skills",
        "incident": "#proactive/incidents",
    }[domain]


def _notice_title(event_type: str) -> str:
    return {
        "proactive.cron.completed": "定时提醒已完成",
        "proactive.breakbeat.completed": "Breakbeat 已完成",
        "proactive.dream.completed": "长期记忆已更新",
        "proactive.skill_evolution.completed": "Skill 演进已完成",
        "proactive.incident.opened": "发现主动能力异常",
        "proactive.incident.resolved": "主动能力已恢复",
    }.get(event_type, "主动能力更新")
