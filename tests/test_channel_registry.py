from __future__ import annotations

import errno
import importlib
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest

Recipient = importlib.import_module("Turning-Good-Agent.bus.messages").Recipient
ChannelAccountRegistry = importlib.import_module(
    "Turning-Good-Agent.channels.registry"
).ChannelAccountRegistry
ChannelConflictError = importlib.import_module(
    "Turning-Good-Agent.channels.registry"
).ChannelConflictError


def registry(data_dir: Path) -> ChannelAccountRegistry:
    return ChannelAccountRegistry(data_dir, owner_principal_id="owner")


def test_registry_view_never_contains_private_protocol_values(tmp_path: Path) -> None:
    store = registry(tmp_path)
    account = store.register_feishu_bot(
        app_id="cli_x", app_secret="secret", domain="open.feishu.cn"
    )

    assert account.status == "awaiting_owner_code"
    view = store.list_views()[0]
    assert view.id == account.id
    assert "secret" not in repr(view)
    assert "cli_x" not in repr(view)
    assert "open.feishu.cn" not in repr(view)
    assert view.credential_state == "configured"
    payload = json.loads(
        (tmp_path / "channels" / "feishu" / f"{account.id}.json").read_text(encoding="utf-8")
    )
    assert payload["version"] == 1
    assert payload["private"]["app_secret"] == "secret"


def test_claim_inbound_is_persistent_and_bounded(tmp_path: Path) -> None:
    store = registry(tmp_path)
    account = store.create_weixin_invitation(principal_id=None)

    assert account.status == "pending_qr"
    assert store.claim_inbound("weixin", account.id, "message-1") is True
    assert store.claim_inbound("weixin", account.id, "message-1") is False
    restarted = registry(tmp_path)
    assert restarted.claim_inbound("weixin", account.id, "message-1") is False

    for index in range(2_049):
        assert store.claim_inbound("weixin", account.id, f"message-{index + 2}") is True
    assert store.claim_inbound("weixin", account.id, "message-1") is True
    payload = json.loads(
        (tmp_path / "channels" / "weixin" / f"{account.id}.json").read_text(encoding="utf-8")
    )
    assert len(payload["private"]["inbound_ids"]) == 2_048


def test_claim_inbounds_is_atomic_when_one_alias_was_seen(tmp_path: Path) -> None:
    store = registry(tmp_path)
    account = store.register_feishu_bot(
        app_id="cli_x", app_secret="secret", domain="open.feishu.cn"
    )

    assert store.claim_inbounds("feishu", account.id, ("event-1", "message-1")) is True
    assert store.claim_inbounds("feishu", account.id, ("event-2", "message-1")) is False
    assert store.claim_inbound("feishu", account.id, "event-2") is True


def test_registry_rejects_duplicate_feishu_app_and_unsafe_ids(tmp_path: Path) -> None:
    store = registry(tmp_path)
    store.register_feishu_bot(app_id="cli_x", app_secret="secret", domain="open.feishu.cn")

    with pytest.raises(ValueError, match="app_id"):
        store.register_feishu_bot(app_id="cli_x", app_secret="other", domain="open.feishu.cn")
    with pytest.raises(ValueError, match="platform"):
        store.get("other", "account")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="account"):
        store.get("weixin", "../escape")  # type: ignore[arg-type]


def test_registry_rejects_duplicate_feishu_app_on_update(tmp_path: Path) -> None:
    store = registry(tmp_path)
    first = store.register_feishu_bot(
        app_id="first-app", app_secret="secret", domain="open.feishu.cn"
    )
    second = store.register_feishu_bot(
        app_id="second-app", app_secret="secret", domain="open.feishu.cn"
    )

    with pytest.raises(ValueError, match="app_id"):
        store.update(replace(second, private={**second.private, "app_id": "first-app"}))

    current = store.get("feishu", second.id)
    assert current is not None
    assert current.private["app_id"] == "second-app"
    assert first.private["app_id"] == "first-app"


def test_registry_validates_duplicate_feishu_candidate_without_persisting(tmp_path: Path) -> None:
    store = registry(tmp_path)
    first = store.register_feishu_bot(
        app_id="first-app", app_secret="secret", domain="open.feishu.cn"
    )
    second = store.register_feishu_bot(
        app_id="second-app", app_secret="secret", domain="open.feishu.cn"
    )

    with pytest.raises(ValueError, match="app_id"):
        store.validate_update(
            replace(second, private={**second.private, "app_id": "first-app"})
        )

    current = store.get("feishu", second.id)
    assert current is not None
    assert current.private["app_id"] == "second-app"
    assert first.private["app_id"] == "first-app"


def test_registry_removes_selected_private_fields_without_replacing_account_state(
    tmp_path: Path,
) -> None:
    store = registry(tmp_path)
    account = store.register_feishu_bot(
        app_id="first-app", app_secret="secret", domain="open.feishu.cn"
    )
    account = store.update(
        replace(
            account,
            private={
                **account.private,
                "owner_code_hash": "hash",
                "owner_code_expires_at": "2026-08-08T00:00:00+00:00",
            },
        )
    )

    updated = store.remove_private_fields(
        "feishu",
        account.id,
        {"owner_code_hash", "owner_code_expires_at"},
    )

    assert "owner_code_hash" not in updated.private
    assert "owner_code_expires_at" not in updated.private
    assert updated.private["app_id"] == "first-app"
    assert updated.private["app_secret"] == "secret"
    assert updated.status == "awaiting_owner_code"


def test_registry_can_replace_private_state_without_merging_removed_identity(
    tmp_path: Path,
) -> None:
    store = registry(tmp_path)
    account = store.register_feishu_bot(
        app_id="first-app", app_secret="secret", domain="open.feishu.cn"
    )
    account = store.update(
        replace(
            account,
            status="active",
            private={
                **account.private,
                "owner_open_id": "old-owner",
                "conversation_id": f"{account.id}:old-chat",
            },
        )
    )

    updated = store.replace_private_state(
        replace(
            account,
            status="awaiting_owner_code",
            private={
                "app_id": "second-app",
                "app_secret": "new-secret",
                "domain": "open.feishu.cn",
                "credential_state": "configured",
                "inbound_ids": [],
            },
        )
    )

    assert updated.private["app_id"] == "second-app"
    assert "owner_open_id" not in updated.private
    assert "conversation_id" not in updated.private


def test_registry_rejects_conditional_update_from_stale_account(tmp_path: Path) -> None:
    store = registry(tmp_path)
    account = store.register_feishu_bot(
        app_id="first-app", app_secret="secret", domain="open.feishu.cn"
    )
    current = store.update(replace(account, subscribed=True))

    with pytest.raises(ValueError, match="账号状态已更新"):
        store.update(
            replace(account, enabled=False, status="disabled"),
            expected_updated_at=account.updated_at,
        )

    assert store.get("feishu", account.id) == current


def test_revoke_preserves_identity_and_private_file(tmp_path: Path) -> None:
    store = registry(tmp_path)
    account = store.create_weixin_invitation(principal_id="guest")
    path = tmp_path / "channels" / "weixin" / f"{account.id}.json"

    revoked = store.revoke("weixin", account.id)
    assert revoked.id == account.id
    assert revoked.principal_id == "guest"
    assert revoked.status == "revoked"
    assert path.exists()
    assert store.get("weixin", account.id).principal_id == "guest"  # type: ignore[union-attr]
    restored = store.update(replace(revoked, status="pending_qr", enabled=True, subscribed=False))
    assert restored.id == account.id
    assert restored.principal_id == "guest"


def test_revoke_uses_directory_then_record_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = registry(tmp_path)
    account = store.create_weixin_invitation(principal_id="guest")
    entries: list[str] = []

    @contextmanager
    def directory_lock() -> object:
        entries.append("directory")
        yield

    @contextmanager
    def record_lock(_platform: str, _account_id: str) -> object:
        entries.append("record")
        yield

    monkeypatch.setattr(store, "_directory_lock", directory_lock)
    monkeypatch.setattr(store, "_record_lock", record_lock)

    store.revoke("weixin", account.id)

    assert entries[:2] == ["directory", "record"]


def test_owner_weixin_binding_is_singleton_across_revoked_records(tmp_path: Path) -> None:
    store = registry(tmp_path)
    first = store.create_weixin_invitation(principal_id="owner")
    store.revoke("weixin", first.id)

    with pytest.raises(ChannelConflictError, match="owner_binding_exists"):
        store.create_weixin_invitation(principal_id="owner")


def test_explicit_independent_weixin_binding_is_singleton(tmp_path: Path) -> None:
    store = registry(tmp_path)
    store.create_weixin_invitation(principal_id="guest")

    with pytest.raises(ChannelConflictError, match="principal_binding_exists"):
        store.create_weixin_invitation(principal_id="guest")


def test_first_weixin_sender_is_compare_and_set(tmp_path: Path) -> None:
    store = registry(tmp_path)
    account = store.update(
        replace(store.create_weixin_invitation(principal_id=None), status="awaiting_first_dm")
    )

    winner, accepted = store.claim_weixin_first_sender(account.id, "wx-a", "ctx-a")
    loser, rejected = store.claim_weixin_first_sender(account.id, "wx-b", "ctx-b")

    assert accepted and not rejected
    assert winner.private["from_user_id"] == loser.private["from_user_id"] == "wx-a"


def test_delete_unlinks_exactly_one_weixin_record(tmp_path: Path) -> None:
    store = registry(tmp_path)
    first = store.create_weixin_invitation(principal_id="guest")
    second = store.create_weixin_invitation(principal_id=None)

    deleted = store.delete("weixin", first.id)

    assert deleted == first
    assert store.get("weixin", first.id) is None
    assert store.get("weixin", second.id) == second


def test_view_derives_principal_kind(tmp_path: Path) -> None:
    store = registry(tmp_path)
    owner = store.create_weixin_invitation(principal_id="owner")
    independent = store.create_weixin_invitation(principal_id=None)

    views = {view.id: view for view in store.list_views()}
    assert views[owner.id].principal_kind == "owner"
    assert views[independent.id].principal_kind == "independent"


def test_subscribed_recipients_are_principal_scoped(tmp_path: Path) -> None:
    store = registry(tmp_path)
    pending = store.create_weixin_invitation(principal_id="guest")
    awaiting = store.update(replace(pending, status="awaiting_first_dm"))
    active = replace(
        awaiting,
        status="active",
        enabled=True,
        subscribed=True,
        private={"conversation_id": "guest-conversation"},
    )
    store.update(active)

    assert store.subscribed_recipients("guest") == (
        Recipient("guest", "weixin", "guest-conversation"),
    )
    assert store.subscribed_recipients("owner") == ()
    assert "guest" in store.principal_ids()


def test_concurrent_claims_keep_json_valid_and_claim_once(tmp_path: Path) -> None:
    store = registry(tmp_path)
    account = store.create_weixin_invitation(principal_id="guest")

    def claim() -> bool:
        return store.claim_inbound("weixin", account.id, "same-message")

    with ThreadPoolExecutor(max_workers=16) as executor:
        results = list(executor.map(lambda _: claim(), range(64)))

    assert sum(results) == 1
    payload = json.loads(
        (tmp_path / "channels" / "weixin" / f"{account.id}.json").read_text(encoding="utf-8")
    )
    assert payload["private"]["inbound_ids"] == ["same-message"]


def test_atomic_write_falls_back_for_ebusy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = registry(tmp_path)
    account = store.create_weixin_invitation(principal_id="guest")
    original_replace = __import__("os").replace

    def busy_replace(source: str | bytes, target: str | bytes) -> None:
        del source, target
        raise OSError(errno.EBUSY, "busy")

    monkeypatch.setattr("os.replace", busy_replace)
    updated = replace(account, subscribed=True)
    store.update(updated)
    monkeypatch.setattr("os.replace", original_replace)
    assert store.get("weixin", account.id).subscribed is True  # type: ignore[union-attr]
