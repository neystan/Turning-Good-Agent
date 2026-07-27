/** 根据触发按钮下方的可用空间选择会话菜单展开方向。 */
export function sessionMenuSide(
  triggerBottom: number,
  viewportHeight: number,
  menuHeight = 152,
  padding = 8,
): "right" | "top" {
  return viewportHeight - triggerBottom < menuHeight + padding ? "top" : "right";
}
