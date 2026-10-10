from __future__ import annotations

import asyncio
import importlib
from pathlib import Path

models = importlib.import_module("Turning-Good-Agent.web.attachments.models")
tool_module = importlib.import_module("Turning-Good-Agent.web.attachments.tool")

AttachmentMetadata = models.AttachmentMetadata
AttachmentOwner = models.AttachmentOwner
ResolvedAttachment = models.ResolvedAttachment
ReadAttachmentTool = tool_module.ReadAttachmentTool


def test_tool_has_bounded_schema():
    schema = ReadAttachmentTool(None, AttachmentOwner("p", "web", "c"), {}).input_schema
    assert schema["properties"]["limit"]["maximum"] == 2000
    assert schema["properties"]["offset"]["minimum"] == 1


def test_tool_rejects_attachment_outside_current_turn(tmp_path: Path):
    owner = AttachmentOwner("p", "web", "c")
    path = tmp_path / "note.txt"
    path.write_text("hello", encoding="utf-8")
    attachment = ResolvedAttachment(AttachmentMetadata("att_allowed", "document", "note.txt", "text/plain", 5), path, "d", owner)
    tool = ReadAttachmentTool(lambda _owner, ids: [attachment] if ids == ["att_allowed"] else [], owner, {"att_allowed"})
    result = asyncio.run(tool.run({"attachment_id": "att_other"}))
    assert "不允许" in result.content
