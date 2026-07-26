import { useCallback, useLayoutEffect, useRef, useState, type HTMLAttributes, type ReactNode } from "react";

type ScrollAreaProps = HTMLAttributes<HTMLDivElement> & {
  children: ReactNode;
};

/** 渲染隐藏原生滚动条并带圆角滑块的通用滚动区域。 */
export function ScrollArea({ children, className, ...props }: ScrollAreaProps) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const [thumb, setThumb] = useState<{ top: number; height: number } | null>(null);

  /** 根据视口与内容高度计算滑块位置。 */
  const updateThumb = useCallback(() => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    const overflow = viewport.scrollHeight - viewport.clientHeight;
    if (overflow <= 0) {
      setThumb(null);
      return;
    }
    const trackHeight = Math.max(0, viewport.clientHeight - 8);
    const height = Math.min(trackHeight, Math.max(18, trackHeight * viewport.clientHeight / viewport.scrollHeight));
    const top = 4 + viewport.scrollTop / overflow * (trackHeight - height);
    setThumb({ top, height });
  }, []);

  useLayoutEffect(() => {
    updateThumb();
    const viewport = viewportRef.current;
    if (!viewport || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(updateThumb);
    observer.observe(viewport);
    const mutationObserver = typeof MutationObserver === "undefined" ? null : new MutationObserver(updateThumb);
    mutationObserver?.observe(viewport, { attributes: true, childList: true, subtree: true });
    return () => {
      observer.disconnect();
      mutationObserver?.disconnect();
    };
  }, [children, updateThumb]);

  return <div {...props} className={`scroll-area ${className || ""}`}><div ref={viewportRef} className="scroll-area-viewport" onScroll={updateThumb}>{children}</div>{thumb && <span className="scroll-area-thumb" style={{ height: thumb.height, transform: `translateY(${thumb.top}px)` }} />}</div>;
}
