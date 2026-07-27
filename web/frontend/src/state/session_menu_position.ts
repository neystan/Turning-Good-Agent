type MenuAnchor = Pick<DOMRect, "top" | "bottom" | "right">;
type MenuViewport = { width: number; height: number };

/** 根据触发按钮位置计算固定会话菜单的可见坐标。 */
export function sessionMenuPosition(
  anchor: MenuAnchor,
  viewport: MenuViewport,
  menuHeight = 136,
  menuWidth = 98,
  padding = 8,
  gap = 2,
): { top: number; left: number } {
  const left = Math.min(anchor.right + gap, viewport.width - menuWidth - padding);
  if (anchor.bottom + menuHeight + gap <= viewport.height - padding) return { top: anchor.bottom + gap, left };
  if (anchor.top >= menuHeight + padding) return { top: anchor.top - menuHeight - gap, left };
  return { top: Math.min(anchor.bottom + gap, viewport.height - menuHeight - padding), left };
}
