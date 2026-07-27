import type { ContextWindow } from "../types";

export type ContextWindowView = {
  available: boolean;
  currentLabel: string;
  limitLabel: string;
  usedPercentLabel: string;
  remainingPercentLabel: string;
  usedPercent: number;
  tone: "empty" | "normal" | "warning" | "danger";
};

/** 将已保存的上下文读数转换为 Composer 可直接展示的视图。 */
export function buildContextWindowView(context: ContextWindow | null): ContextWindowView {
  if (!context || context.max_context_tokens <= 0) {
    return {
      available: false,
      currentLabel: "",
      limitLabel: "",
      usedPercentLabel: "",
      remainingPercentLabel: "",
      usedPercent: 0,
      tone: "empty",
    };
  }
  const usedPercent = Math.min(100, Math.max(0, context.current_context_tokens / context.max_context_tokens * 100));
  return {
    available: true,
    currentLabel: formatCompactTokens(context.current_context_tokens),
    limitLabel: formatCompactTokens(context.max_context_tokens),
    usedPercentLabel: formatPercent(usedPercent),
    remainingPercentLabel: formatPercent(100 - usedPercent),
    usedPercent,
    tone: usedPercent >= 90 ? "danger" : usedPercent >= 70 ? "warning" : "normal",
  };
}

/** 使用紧凑单位展示 token 数，避免 Tooltip 读数冗长。 */
function formatCompactTokens(value: number): string {
  const tokens = Math.max(0, Math.round(value));
  if (tokens < 1_000) return String(tokens);
  return `${formatDecimal(tokens / 1_000)}k`;
}

/** 将百分比保留一位小数，并去除无意义尾零。 */
function formatPercent(value: number): string {
  return `${formatDecimal(value)}%`;
}

/** 输出最多一位小数的紧凑数字。 */
function formatDecimal(value: number): string {
  return Number(value.toFixed(1)).toString();
}
