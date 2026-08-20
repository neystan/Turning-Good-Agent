import type { PendingAttachment } from "./types";

const documentExtensions = new Set([".pdf", ".docx", ".xlsx", ".pptx", ".txt", ".md", ".csv", ".json"]);
const imageExtensions = new Set([".png", ".jpg", ".jpeg", ".webp"]);

export function validateSelectedFiles(files: File[], current: PendingAttachment[] = []): PendingAttachment[] {
  const all = [...current, ...files.map((file) => {
    const suffix = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
    const source = imageExtensions.has(suffix) ? "image" : documentExtensions.has(suffix) ? "document" : null;
    const error = !source ? "不支持的附件格式" : source === "image" && file.size > 10 * 1024 * 1024 ? "单张图片不能超过 10 MB" : source === "document" && file.size > 50 * 1024 * 1024 ? "单个文档不能超过 50 MB" : undefined;
    return { id: crypto.randomUUID(), file, source: source || "document", status: error ? "failed" : "pending", error } as PendingAttachment;
  })];
  const documents = all.filter((item) => item.source === "document");
  const images = all.filter((item) => item.source === "image");
  if (documents.length > 5) documents.slice(5).forEach((item) => { item.status = "failed"; item.error = "单条消息最多上传 5 个文档"; });
  if (images.length > 5) images.slice(5).forEach((item) => { item.status = "failed"; item.error = "单条消息最多上传 5 张图片"; });
  if (all.reduce((sum, item) => sum + item.file.size, 0) > 100 * 1024 * 1024) all.forEach((item) => { if (item.status === "pending") { item.status = "failed"; item.error = "单条消息附件总大小不能超过 100 MB"; } });
  return all;
}
