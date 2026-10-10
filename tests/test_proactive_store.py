from __future__ import annotations

import importlib


store_module = importlib.import_module("Turning-Good-Agent.proactive.store")
types_module = importlib.import_module("Turning-Good-Agent.proactive.types")

ProactiveStore = store_module.ProactiveStore
UsageTotals = types_module.UsageTotals


def test_store_round_trips_a_json_snapshot(tmp_path) -> None:
    store = ProactiveStore(tmp_path)
    snapshot = {"items": [{"id": "one"}], "usage": {"calls": 0}}

    store.write_json("state.json", snapshot)

    assert store.read_json("state.json", {}) == snapshot
    assert (tmp_path / "proactive" / "state.json").is_file()


def test_store_falls_back_to_flushed_direct_write_when_replace_is_blocked(tmp_path, monkeypatch) -> None:
    store = ProactiveStore(tmp_path)

    def blocked_replace(source, target) -> None:
        del source, target
        raise PermissionError("bind mount blocks replacement")

    monkeypatch.setattr(store_module.os, "replace", blocked_replace)
    store.write_json("state.json", {"value": 1})

    assert store.read_json("state.json", {}) == {"value": 1}


def test_usage_totals_accumulates_attempts_and_tokens() -> None:
    total = UsageTotals()

    updated = total.add(calls=1, input_tokens=12, output_tokens=3)

    assert updated.to_dict() == {
        "calls": 1,
        "input_tokens": 12,
        "output_tokens": 3,
        "total_tokens": 15,
    }


def test_usage_totals_preserves_provider_total_when_split_is_unavailable() -> None:
    total = UsageTotals()

    updated = total.add(calls=1, total_tokens=3)

    assert updated.to_dict() == {
        "calls": 1,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 3,
    }
