from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class AttachmentOwner:
    principal_id: str
    channel: str
    conversation_id: str


@dataclass(frozen=True, slots=True)
class AttachmentMetadata:
    attachment_id: str
    source: str
    filename: str
    mime_type: str
    size_bytes: int
    processing: str = "ready"
    text_chars: int | None = None
    truncated: bool = False
    error: str | None = None

    def public_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "attachment_id": self.attachment_id,
            "source": self.source,
            "filename": self.filename,
            "mime_type": self.mime_type,
            "size_bytes": self.size_bytes,
            "processing": self.processing,
            "truncated": self.truncated,
            "error": self.error,
        }
        if self.text_chars is not None:
            result["text_chars"] = self.text_chars
        return result


@dataclass(frozen=True, slots=True)
class ResolvedAttachment:
    metadata: AttachmentMetadata
    path: Path
    digest: str
    owner: AttachmentOwner
