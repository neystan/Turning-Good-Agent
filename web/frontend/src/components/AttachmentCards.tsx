import { useEffect, useRef, useState } from "react";
import { FileText, Image as ImageIcon, X } from "lucide-react";
import type { AttachmentMetadata, PendingAttachment } from "../types";

export function PendingAttachmentCards({ items, onRemove }: { items: PendingAttachment[]; onRemove: (id: string) => void }) {
  if (!items.length) return null;
  return <div className="attachment-cards" aria-label="待发送附件">{items.map((item) => <div className={`attachment-card is-${item.status}`} key={item.id}><span className={`attachment-file-visual is-${item.source}`}>{item.source === "image" ? <PendingImagePreview file={item.file} /> : <FileText size={18} aria-hidden="true" />}</span><span className="attachment-copy"><span className="attachment-name" title={item.file.name}>{item.file.name}</span><span className={item.error ? "attachment-error" : "attachment-size"}>{item.error || statusLabel(item)}</span></span><button className="icon-button attachment-remove" type="button" aria-label={`移除附件 ${item.file.name}`} onClick={() => onRemove(item.id)}><X size={14} /></button></div>)}</div>;
}

export function SentAttachmentCards({ attachments, sessionId }: { attachments: AttachmentMetadata[]; sessionId?: string }) {
  const [preview, setPreview] = useState<{ url: string; filename: string } | null>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const triggerRef = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (!preview) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") closePreview();
    };
    window.addEventListener("keydown", onKeyDown);
    closeButtonRef.current?.focus();
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [preview]);
  if (!attachments.length) return null;
  function closePreview() {
    setPreview(null);
    window.requestAnimationFrame(() => (document.querySelector<HTMLElement>('[aria-label="消息内容"]') || triggerRef.current)?.focus());
  }
  return <><div className="sent-attachment-cards">{attachments.map((item) => {
    const previewUrl = sessionId ? `/api/control/sessions/${encodeURIComponent(sessionId)}/attachments/${encodeURIComponent(item.attachment_id)}/preview` : null;
    return item.source === "image" && previewUrl ? <button className="sent-attachment image" key={item.attachment_id} type="button" onClick={(event) => { triggerRef.current = event.currentTarget; setPreview({ url: previewUrl, filename: item.filename }); }}><img src={previewUrl} alt="" /><span className="sent-attachment-copy"><strong title={item.filename}>{item.filename}</strong><small>{formatBytes(item.size_bytes)}</small></span></button> : <div className="sent-attachment document" key={item.attachment_id}><span className="attachment-file-visual is-document"><FileText size={18} aria-hidden="true" /></span><span className="sent-attachment-copy"><strong title={item.filename}>{item.filename}</strong><small>{formatBytes(item.size_bytes)}</small></span></div>;
  })}</div>{preview && <div className="attachment-preview" role="dialog" aria-modal="true" aria-label="图片预览" onClick={closePreview}><figure onClick={(event) => event.stopPropagation()}><img src={preview.url} alt={preview.filename} /><figcaption>{preview.filename}</figcaption></figure><button ref={closeButtonRef} className="icon-button" type="button" aria-label="关闭图片预览" onClick={closePreview}><X size={18} /></button></div>}</>;
}

function PendingImagePreview({ file }: { file: File }) {
  const [url, setUrl] = useState("");
  useEffect(() => {
    const nextUrl = URL.createObjectURL(file);
    setUrl(nextUrl);
    return () => URL.revokeObjectURL(nextUrl);
  }, [file]);
  return url ? <img src={url} alt="" /> : <ImageIcon size={18} aria-hidden="true" />;
}

function statusLabel(item: PendingAttachment): string {
  if (item.status === "uploading") return "正在上传";
  if (item.status === "ready") return "已就绪";
  return formatBytes(item.file.size);
}

function formatBytes(bytes: number): string { return bytes < 1024 ? `${bytes} B` : bytes < 1024 * 1024 ? `${Math.round(bytes / 1024)} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`; }
