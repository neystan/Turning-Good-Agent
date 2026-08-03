from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading

ownership_module = __import__(
    "Turning-Good-Agent.web.backend.proactive_ownership",
    fromlist=["ProactiveOwnershipLease"],
)

ProactiveOwnershipLease = ownership_module.ProactiveOwnershipLease


def test_first_owner_excludes_second_host_then_releases(tmp_path: Path) -> None:
    async def run() -> None:
        first = ProactiveOwnershipLease(tmp_path, owner_kind="web", owner_id="first")
        second = ProactiveOwnershipLease(tmp_path, owner_kind="cli", owner_id="second")

        assert (await first.acquire_owner()).mode == "owner"
        readonly = await second.acquire_owner()
        assert readonly.mode == "readonly"
        assert readonly.owner_id == "first"

        await first.release_owner()
        assert (await second.try_takeover()).mode == "owner"
        await second.release_owner()

    asyncio.run(run())


def test_stale_owner_record_can_be_taken_over(tmp_path: Path) -> None:
    async def run() -> None:
        path = tmp_path / "proactive" / ".owner.json"
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps({"owner_id": "dead", "owner_kind": "cli"}),
            encoding="utf-8",
        )
        lease = ProactiveOwnershipLease(tmp_path, owner_kind="web", owner_id="web")

        state = await lease.acquire_owner()

        assert state.mode == "owner"
        assert state.owner_id == "web"
        await lease.release_owner()

    asyncio.run(run())


def test_partial_or_unreadable_live_record_is_never_deleted_for_takeover(tmp_path: Path) -> None:
    async def run() -> None:
        first = ProactiveOwnershipLease(tmp_path, owner_kind="web", owner_id="first", heartbeat_seconds=60)
        second = ProactiveOwnershipLease(tmp_path, owner_kind="cli", owner_id="second", heartbeat_seconds=60)
        assert (await first.acquire_owner()).writable is True
        path = tmp_path / "proactive" / ".owner.json"
        path.write_text("{", encoding="utf-8")

        readonly = await second.try_takeover()

        assert readonly.writable is False
        assert path.read_text(encoding="utf-8") == "{"
        await first.release_owner()

    asyncio.run(run())


def test_heartbeat_replaces_complete_json_atomically(tmp_path: Path) -> None:
    async def run() -> None:
        lease = ProactiveOwnershipLease(tmp_path, owner_kind="web", owner_id="owner", heartbeat_seconds=0.001)
        assert (await lease.acquire_owner()).writable is True
        path = tmp_path / "proactive" / ".owner.json"
        before = json.loads(path.read_text(encoding="utf-8"))
        for _ in range(50):
            await asyncio.sleep(0.002)
            current = json.loads(path.read_text(encoding="utf-8"))
            if current["heartbeat_at"] != before["heartbeat_at"]:
                break
        assert current["owner_id"] == "owner"
        assert current["heartbeat_at"] != before["heartbeat_at"]
        await lease.release_owner()

    asyncio.run(run())


def test_concurrent_stale_takeover_keeps_exactly_one_owner(tmp_path: Path) -> None:
    stale_path = tmp_path / "proactive" / ".owner.json"
    stale_path.parent.mkdir(parents=True)
    stale_path.write_text(
        json.dumps({"owner_id": "dead", "owner_kind": "cli"}),
        encoding="utf-8",
    )
    first_at_compare = threading.Event()
    second_acquired = threading.Event()

    class DelayedRecord(dict[str, object]):
        def __eq__(self, other: object) -> bool:
            first_at_compare.set()
            second_acquired.wait(timeout=1)
            return super().__eq__(other)

        def __ne__(self, other: object) -> bool:
            return not self.__eq__(other)

    class DelayedLease(ProactiveOwnershipLease):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self._reads = 0

        def _read_record(self):
            value = super()._read_record()
            self._reads += 1
            if self._reads == 2 and isinstance(value, dict):
                return DelayedRecord(value)
            return value

    first = DelayedLease(tmp_path, owner_kind="web", owner_id="first", heartbeat_seconds=60)
    second = ProactiveOwnershipLease(tmp_path, owner_kind="cli", owner_id="second", heartbeat_seconds=60)

    async def acquire_without_heartbeat(lease: ProactiveOwnershipLease):
        state = await lease.acquire_owner()
        if lease._heartbeat_task is not None:
            lease._heartbeat_task.cancel()
            await asyncio.gather(lease._heartbeat_task, return_exceptions=True)
            lease._heartbeat_task = None
        return state

    def acquire_first():
        return asyncio.run(acquire_without_heartbeat(first))

    def acquire_second():
        first_at_compare.wait(timeout=0.1)
        state = asyncio.run(acquire_without_heartbeat(second))
        if state.writable:
            second_acquired.set()
        return state

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(acquire_first)
        second_future = pool.submit(acquire_second)
        first_state = first_future.result()
        second_state = second_future.result()

    assert sum(state.writable for state in (first_state, second_state)) == 1
    asyncio.run(first.release_owner())
    asyncio.run(second.release_owner())
