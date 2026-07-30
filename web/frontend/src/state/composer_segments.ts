import type { ComposerGuidanceSegment, ComposerSegment, ComposerTextSegment } from "../types";

export function createTextSegment(text = ""): ComposerTextSegment {
  return { type: "text", id: crypto.randomUUID(), text };
}

export function createGuidanceSegment(entry: ComposerGuidanceSegment["entry"]): ComposerGuidanceSegment {
  return { type: "guidance", id: crypto.randomUUID(), entry };
}

export function composerPlainText(segments: ComposerSegment[]): string {
  return segments.filter((segment): segment is Extract<ComposerSegment, { type: "text" }> => segment.type === "text").map((segment) => segment.text).join("");
}

export function serializeComposerContent(segments: ComposerSegment[]): string {
  return segments.map((segment) => segment.type === "text" ? segment.text : segment.entry.insert_text).join("").trim();
}
