import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

import { placeOverlay } from "../state/overlay_position";

type OverlayPortalProps = {
  children: ReactNode;
  onDismiss: () => void;
  anchor?: Pick<DOMRect, "right" | "bottom">;
  className?: string;
};

/** 将菜单或对话框渲染到 body，避开局部滚动与层叠裁切。 */
export function OverlayPortal({ children, onDismiss, anchor, className = "" }: OverlayPortalProps) {
  const rootRef = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState({ top: -10_000, left: -10_000 });

  useEffect(() => {
    /** 在浮层外点击或按 Escape 时关闭浮层。 */
    const dismissFromDocument = (event: PointerEvent | KeyboardEvent) => {
      if (event instanceof KeyboardEvent && event.key === "Escape") onDismiss();
      if (event instanceof PointerEvent && rootRef.current && !rootRef.current.contains(event.target as Node)) onDismiss();
    };
    document.addEventListener("pointerdown", dismissFromDocument);
    document.addEventListener("keydown", dismissFromDocument);
    return () => {
      document.removeEventListener("pointerdown", dismissFromDocument);
      document.removeEventListener("keydown", dismissFromDocument);
    };
  }, [onDismiss]);

  useLayoutEffect(() => {
    /** 根据浮层实际尺寸和视口变化更新锚点位置。 */
    const updatePosition = () => {
      if (!anchor || !rootRef.current) return;
      setPosition(placeOverlay(anchor, rootRef.current.getBoundingClientRect(), { width: window.innerWidth, height: window.innerHeight }));
    };
    updatePosition();
    window.addEventListener("resize", updatePosition);
    window.addEventListener("scroll", updatePosition, true);
    return () => {
      window.removeEventListener("resize", updatePosition);
      window.removeEventListener("scroll", updatePosition, true);
    };
  }, [anchor]);

  const style = anchor ? { top: position.top, left: position.left } : undefined;
  return createPortal(
    <div
      ref={rootRef}
      className={`overlay-portal ${anchor ? "is-anchored" : "is-modal"} ${className}`}
      data-overlay-root
      style={style}
      onPointerDown={(event) => {
        if (!anchor && event.target === event.currentTarget) onDismiss();
      }}
    >
      {children}
    </div>,
    document.body,
  );
}
