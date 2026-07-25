type OverlaySize = {
  width: number;
  height: number;
};

type ViewportSize = {
  width: number;
  height: number;
};

const VIEWPORT_MARGIN = 8;

/** 将浮层坐标限制在当前视口的安全边距内。 */
export function placeOverlay(
  anchor: Pick<DOMRect, "right" | "bottom">,
  overlay: OverlaySize,
  viewport: ViewportSize,
): { top: number; left: number } {
  return {
    top: clamp(anchor.bottom + VIEWPORT_MARGIN, VIEWPORT_MARGIN, viewport.height - overlay.height - VIEWPORT_MARGIN),
    left: clamp(anchor.right - overlay.width, VIEWPORT_MARGIN, viewport.width - overlay.width - VIEWPORT_MARGIN),
  };
}

/** 将数值限制在给定范围内。 */
function clamp(value: number, minimum: number, maximum: number): number {
  return Math.max(minimum, Math.min(value, Math.max(minimum, maximum)));
}
