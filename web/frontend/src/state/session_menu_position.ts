type MenuAnchor = Pick<DOMRect, "top" | "bottom" | "right">;
type MenuViewport = { width: number; height: number };

/** 根据触发按钮位置计算固定会话菜单的可见坐标。 */
export function sessionMenuPosition(
  anchor: MenuAnchor,
  viewport: MenuViewport,
  menuHeight = 128,
  menuWidth = 98,
  padding = 8,
  gap = 2,
): { top: number; left: number } {
  const left = Math.min(anchor.right + gap, viewport.width - menuWidth - padding);
  if (anchor.top + menuHeight <= viewport.height - padding) return { top: anchor.top, left };
  if (anchor.bottom >= menuHeight + padding) return { top: anchor.bottom - menuHeight, left };
  return { top: Math.min(anchor.bottom + gap, viewport.height - menuHeight - padding), left };
}
