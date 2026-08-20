from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ...tools.base import ToolResult
from .documents import AttachmentReadError, read_document
from .models import AttachmentOwner, ResolvedAttachment


class ReadAttachmentTool:
    name = "read_attachment"
    source = "web_attachment"
    description = "按需读取当前 Web 消息中的文档附件文本；不读取图片。"
    parallel_safe = True
    worker_read_only = False
    input_schema = {
        "type": "object",
        "properties": {
            "attachment_id": {"type": "string"},
            "offset": {"type": "integer", "minimum": 1, "default": 1},
            "limit": {"type": "integer", "minimum": 1, "maximum": 2000, "default": 2000},
            "pages": {"type": ["string", "null"]},
            "force": {"type": "boolean", "default": False},
        },
        "required": ["attachment_id"],
    }

    def __init__(self, resolver: Any, owner: AttachmentOwner, attachments: dict[str, ResolvedAttachment] | set[str]) -> None:
        self._resolver = resolver
        self._owner = owner
        self._attachments = attachments
        self._seen: set[tuple[Any, ...]] = set()

    async def run(self, args: dict[str, Any]) -> ToolResult:
        attachment_id = args.get("attachment_id")
        if not isinstance(attachment_id, str) or attachment_id not in self._attachments:
            return ToolResult("读取附件失败：当前回合不允许访问该附件。", {"error_code": "not_allowed"})
        key = (attachment_id, args.get("offset", 1), args.get("limit", 2000), args.get("pages"))
        if key in self._seen and not args.get("force", False):
            return ToolResult("该附件范围已读取；如需重新读取请设置 force=true。", {"deduplicated": True})
        self._seen.add(key)
        try:
            attachment = self._attachments[attachment_id] if isinstance(self._attachments, dict) else self._resolver(self._owner, [attachment_id])[0]
            result = read_document(attachment, offset=int(args.get("offset", 1)), limit=int(args.get("limit", 2000)), pages=args.get("pages"))
            return ToolResult(result.content, {"attachment_id": attachment_id, "filename": attachment.metadata.filename, "range": result.range_label, "has_more": result.has_more, "truncated": result.truncated})
        except AttachmentReadError as exc:
            return ToolResult(f"读取附件失败：{exc}", {"attachment_id": attachment_id, "error_code": "read_error"})
