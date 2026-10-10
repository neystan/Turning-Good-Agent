from __future__ import annotations

from dataclasses import dataclass
import importlib
import asyncio

import pytest

models = importlib.import_module("Turning-Good-Agent.web.attachments.models")
store = importlib.import_module("Turning-Good-Agent.web.attachments.store")
validation = importlib.import_module("Turning-Good-Agent.web.attachments.validation")

AttachmentOwner = models.AttachmentOwner
AttachmentNotFoundError = store.AttachmentNotFoundError
WebAttachmentService = store.WebAttachmentService
AttachmentValidationError = validation.AttachmentValidationError


@dataclass
class Upload:
    filename: str
    payload: bytes
    content_type: str

    async def read(self) -> bytes:
        return self.payload


def test_upload_batch_is_atomic_when_a_file_is_spoofed(tmp_path):
    service = WebAttachmentService(tmp_path)
    owner = AttachmentOwner("owner", "web", "conversation")
    files = [Upload("ok.txt", b"ok", "text/plain"), Upload("fake.png", b"not png", "image/png")]

    with pytest.raises(AttachmentValidationError, match="签名"):
        asyncio.run(service.upload_batch(owner, files))

    assert list((tmp_path / "web-attachments").rglob("content")) == []


def test_attachment_id_cannot_cross_conversations(tmp_path):
    service = WebAttachmentService(tmp_path)
    first = AttachmentOwner("owner", "web", "first")
    second = AttachmentOwner("owner", "web", "second")
    [metadata] = asyncio.run(service.upload_batch(first, [Upload("ok.txt", b"ok", "text/plain")]))

    with pytest.raises(AttachmentNotFoundError, match="附件不存在"):
        service.resolve(second, [metadata.attachment_id])


def test_public_metadata_never_contains_storage_path_or_bytes(tmp_path):
    service = WebAttachmentService(tmp_path)
    owner = AttachmentOwner("owner", "web", "conversation")
    [metadata] = asyncio.run(service.upload_batch(owner, [Upload("ok.txt", b"hello", "text/plain")]))

    public = metadata.public_dict()
    assert public["source"] == "document"
    assert public["filename"] == "ok.txt"
    assert "path" not in public
    assert "content" not in public
    assert b"hello" not in public.values()


def test_batch_count_limits_documents_and_images_independently(tmp_path):
    service = WebAttachmentService(tmp_path)
    owner = AttachmentOwner("owner", "web", "conversation")
    files = [Upload(f"{index}.txt", b"x", "text/plain") for index in range(6)]

    with pytest.raises(AttachmentValidationError, match="最多上传 5 个文档"):
        asyncio.run(service.upload_batch(owner, files))


def test_image_preview_rejects_documents(tmp_path):
    service = WebAttachmentService(tmp_path)
    owner = AttachmentOwner("owner", "web", "conversation")
    [metadata] = asyncio.run(service.upload_batch(owner, [Upload("ok.txt", b"hello", "text/plain")]))

    with pytest.raises(AttachmentValidationError, match="不支持预览"):
        service.open_image(owner, metadata.attachment_id)
