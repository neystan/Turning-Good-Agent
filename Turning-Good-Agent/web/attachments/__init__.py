"""Web-only attachment storage and bounded readers."""

from .models import AttachmentMetadata, AttachmentOwner, ResolvedAttachment
from .store import WebAttachmentService

__all__ = ["AttachmentMetadata", "AttachmentOwner", "ResolvedAttachment", "WebAttachmentService"]
