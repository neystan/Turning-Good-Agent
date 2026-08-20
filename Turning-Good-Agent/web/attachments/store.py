from __future__ import annotations

import hashlib
import json
import secrets
import shutil
from pathlib import Path
from typing import Any, Sequence

from .models import AttachmentMetadata, AttachmentOwner, ResolvedAttachment
from .validation import AttachmentValidationError, classify_upload, validate_batch


class AttachmentNotFoundError(LookupError):
    pass


class WebAttachmentService:
    """Persist Web attachments in an owner/session-scoped private directory."""

    def __init__(self, data_dir: Path | str) -> None:
        self.root = Path(data_dir) / "web-attachments"
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _scope(owner: AttachmentOwner) -> str:
        raw = "\0".join((owner.principal_id, owner.channel, owner.conversation_id)).encode()
        return hashlib.sha256(raw).hexdigest()

    async def upload_batch(self, owner: AttachmentOwner, files: Sequence[Any]) -> list[AttachmentMetadata]:
        uploads: list[tuple[str, str | None, bytes]] = []
        for upload in files:
            filename = Path(str(getattr(upload, "filename", "") or "")).name
            uploads.append((filename, getattr(upload, "content_type", None), await upload.read()))
        validate_batch(uploads)
        classifications = [classify_upload(filename, mime, raw) for filename, mime, raw in uploads]
        scope_dir = self.root / self._scope(owner)
        staging = self.root / f".staging-{secrets.token_urlsafe(18)}"
        moved: list[Path] = []
        created: list[AttachmentMetadata] = []
        try:
            staging.mkdir(parents=True)
            for (filename, _declared, raw), (source, mime_type) in zip(uploads, classifications):
                attachment_id = "att_" + secrets.token_urlsafe(24)
                item_dir = staging / attachment_id
                item_dir.mkdir()
                digest = hashlib.sha256(raw).hexdigest()
                (item_dir / "content").write_bytes(raw)
                metadata = AttachmentMetadata(attachment_id, source, filename, mime_type, len(raw))
                manifest = {
                    "owner": {"principal_id": owner.principal_id, "channel": owner.channel, "conversation_id": owner.conversation_id},
                    "metadata": metadata.public_dict(),
                    "digest": digest,
                }
                (item_dir / "metadata.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
                created.append(metadata)
            scope_dir.mkdir(parents=True, exist_ok=True)
            for metadata in created:
                target = scope_dir / metadata.attachment_id
                shutil.move(str(staging / metadata.attachment_id), str(target))
                moved.append(target)
            return created
        except Exception:
            for path in moved:
                shutil.rmtree(path, ignore_errors=True)
            raise
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def resolve(self, owner: AttachmentOwner, attachment_ids: Sequence[str]) -> list[ResolvedAttachment]:
        result: list[ResolvedAttachment] = []
        scope_dir = self.root / self._scope(owner)
        for attachment_id in attachment_ids:
            if not isinstance(attachment_id, str) or not attachment_id.startswith("att_") or "/" in attachment_id or "\\" in attachment_id:
                raise AttachmentNotFoundError("附件不存在")
            item_dir = scope_dir / attachment_id
            manifest_path = item_dir / "metadata.json"
            content_path = item_dir / "content"
            if not manifest_path.is_file() or not content_path.is_file():
                raise AttachmentNotFoundError("附件不存在")
            try:
                payload = json.loads(manifest_path.read_text(encoding="utf-8"))
                stored_owner = payload["owner"]
                expected_owner = {"principal_id": owner.principal_id, "channel": owner.channel, "conversation_id": owner.conversation_id}
                if stored_owner != expected_owner:
                    raise AttachmentNotFoundError("附件不存在")
                metadata = AttachmentMetadata(**payload["metadata"])
                digest = str(payload["digest"])
            except AttachmentNotFoundError:
                raise
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise AttachmentNotFoundError("附件不存在") from exc
            result.append(ResolvedAttachment(metadata, content_path, digest, owner))
        return result

    def open_image(self, owner: AttachmentOwner, attachment_id: str) -> ResolvedAttachment:
        resolved = self.resolve(owner, [attachment_id])[0]
        if resolved.metadata.source != "image":
            raise AttachmentValidationError("文档不支持预览")
        return resolved

    def delete_session(self, owner: AttachmentOwner) -> None:
        shutil.rmtree(self.root / self._scope(owner), ignore_errors=True)
