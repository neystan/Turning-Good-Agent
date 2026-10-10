from __future__ import annotations

import importlib
import json
from pathlib import Path

from fastapi.testclient import TestClient


def _client(tmp_path: Path) -> TestClient:
    settings_module = importlib.import_module("Turning-Good-Agent.config.settings")
    runtime_module = importlib.import_module("Turning-Good-Agent.runtime.runtime")
    gateway_module = importlib.import_module("Turning-Good-Agent.gateway.host")
    app_module = importlib.import_module("Turning-Good-Agent.web.backend.app")
    settings = settings_module.Settings(data_dir=tmp_path / "state")
    settings.proactive.enabled = False
    settings.local_config_path = tmp_path / "settings.local.json"
    settings.local_config_path.write_text(json.dumps({"data_dir": "state"}), encoding="utf-8")
    runtime = runtime_module.AgentRuntime.create_default(settings, object())
    return TestClient(app_module.create_app(gateway_module.GatewayHost(settings, runtime=runtime)))


def test_web_attachment_http_upload_preview_and_session_scope(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        first = client.post("/api/sessions")
        assert first.status_code == 200
        first_session = first.json()
        second_session = client.post("/api/sessions").json()

        upload = client.post(
            f"/api/control/sessions/{first_session['id']}/attachments",
            files=[
                ("files", ("note.txt", b"hello\nworld", "text/plain")),
                ("files", ("photo.png", b"\x89PNG\r\n\x1a\nbody", "image/png")),
            ],
        )
        assert upload.status_code == 200
        metadata = upload.json()["attachments"]
        assert {item["filename"] for item in metadata} == {"note.txt", "photo.png"}
        assert all("path" not in item and "base64" not in item for item in metadata)

        image = next(item for item in metadata if item["source"] == "image")
        document = next(item for item in metadata if item["source"] == "document")
        preview = client.get(
            f"/api/control/sessions/{first_session['id']}/attachments/{image['attachment_id']}/preview"
        )
        assert preview.status_code == 200
        assert preview.headers["cache-control"] == "private, no-store"
        assert preview.headers["x-content-type-options"] == "nosniff"

        cross_session = client.get(
            f"/api/control/sessions/{second_session['id']}/attachments/{image['attachment_id']}/preview"
        )
        assert cross_session.status_code == 404

        document_preview = client.get(
            f"/api/control/sessions/{first_session['id']}/attachments/{document['attachment_id']}/preview"
        )
        assert document_preview.status_code == 415

        attachment_root = Path(client.app.state.gateway.attachment_service.root)
        assert list(attachment_root.rglob(document["attachment_id"]))
        deleted = client.delete(f"/api/sessions/{first_session['id']}")
        assert deleted.status_code == 204
        assert not list(attachment_root.rglob(document["attachment_id"]))
