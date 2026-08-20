from __future__ import annotations

from io import BytesIO
from pathlib import Path
from zipfile import BadZipFile, ZipFile


DOCUMENT_EXTENSIONS = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".json": "application/json",
}
IMAGE_EXTENSIONS = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
MAX_DOCUMENT_BYTES = 50 * 1024 * 1024
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_BATCH_BYTES = 100 * 1024 * 1024
MAX_DOCUMENTS = 5
MAX_IMAGES = 5


class AttachmentValidationError(ValueError):
    pass


def _magic_mime(header: bytes) -> str | None:
    if header.startswith(b"%PDF-"):
        return "application/pdf"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return "image/webp"
    if header.startswith(b"PK\x03\x04"):
        return "application/zip"
    return None


def _validate_ooxml(raw: bytes, suffix: str) -> None:
    expected_prefix = {".docx": "word/", ".xlsx": "xl/", ".pptx": "ppt/"}[suffix]
    try:
        with ZipFile(BytesIO(raw)) as archive:
            names = {item.filename for item in archive.infolist()}
    except BadZipFile as exc:
        raise AttachmentValidationError("Office 文档文件签名无效") from exc
    if "[Content_Types].xml" not in names or not any(name.startswith(expected_prefix) for name in names):
        raise AttachmentValidationError("Office 文档内容与扩展名不一致")


def classify_upload(filename: str, content_type: str | None, raw: bytes) -> tuple[str, str]:
    suffix = Path(filename or "").suffix.lower()
    expected = DOCUMENT_EXTENSIONS.get(suffix) or IMAGE_EXTENSIONS.get(suffix)
    if expected is None:
        raise AttachmentValidationError("不支持的附件格式")
    size = len(raw)
    if size <= 0:
        raise AttachmentValidationError("附件不能为空")
    if suffix in DOCUMENT_EXTENSIONS and size > MAX_DOCUMENT_BYTES:
        raise AttachmentValidationError("单个文档不能超过 50 MB")
    if suffix in IMAGE_EXTENSIONS and size > MAX_IMAGE_BYTES:
        raise AttachmentValidationError("单张图片不能超过 10 MB")
    detected = _magic_mime(raw[:32])
    if suffix in IMAGE_EXTENSIONS and detected != expected:
        raise AttachmentValidationError("图片 MIME、扩展名或文件签名不一致")
    if suffix == ".pdf" and detected != expected:
        raise AttachmentValidationError("PDF MIME、扩展名或文件签名不一致")
    if suffix in {".docx", ".xlsx", ".pptx"}:
        if detected != "application/zip":
            raise AttachmentValidationError("Office 文档文件签名无效")
        _validate_ooxml(raw, suffix)
    declared = (content_type or "").split(";", 1)[0].strip().lower()
    allowed_declared = {expected, "application/octet-stream", ""}
    if suffix in {".docx", ".xlsx", ".pptx"}:
        allowed_declared.add("application/zip")
    if suffix in {".txt", ".md", ".csv", ".json"}:
        allowed_declared.update({"text/plain", "text/markdown", "text/csv", "application/json"})
    if declared not in allowed_declared:
        raise AttachmentValidationError("附件 MIME 与扩展名不一致")
    return ("image" if suffix in IMAGE_EXTENSIONS else "document", expected)


def validate_batch(items: list[tuple[str, str | None, bytes]]) -> None:
    documents = sum(1 for filename, *_ in items if Path(filename).suffix.lower() in DOCUMENT_EXTENSIONS)
    images = sum(1 for filename, *_ in items if Path(filename).suffix.lower() in IMAGE_EXTENSIONS)
    if documents > MAX_DOCUMENTS:
        raise AttachmentValidationError("单条消息最多上传 5 个文档")
    if images > MAX_IMAGES:
        raise AttachmentValidationError("单条消息最多上传 5 张图片")
    if sum(len(raw) for _, _, raw in items) > MAX_BATCH_BYTES:
        raise AttachmentValidationError("单条消息附件总大小不能超过 100 MB")
