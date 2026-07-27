type MenuAnchor = Pick<DOMRect, "top" | "bottom" | "right">;
type MenuViewport = { width: number; height: number };

/** 根据触发按钮位置计算固定会话菜单的可见坐标。 */
export function sessionMenuPosition(
  anchor: MenuAnchor,
  viewport: MenuViewport,
  menuHeight = 136,
  padding = 8,
  gap = 6,
): { top: number; right: number } {
  const right = Math.max(padding, viewport.width - anchor.right);
  if (anchor.top >= menuHeight + padding) return { top: anchor.top - menuHeight - gap, right };
  return { top: Math.min(anchor.bottom + gap, viewport.height - menuHeight - padding), right };
}
