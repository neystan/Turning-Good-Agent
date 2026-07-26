import * as Tooltip from "@radix-ui/react-tooltip";
import type { ReactElement } from "react";

type IconTooltipProps = {
  label: string;
  children: ReactElement;
};

/** 保留原图标按钮作为触发器，统一提供悬浮说明。 */
export function IconTooltip({ label, children }: IconTooltipProps) {
  return <Tooltip.Root>
    <Tooltip.Trigger asChild>{children}</Tooltip.Trigger>
    <Tooltip.Portal>
      <Tooltip.Content className="icon-tooltip" sideOffset={6}>{label}<Tooltip.Arrow className="icon-tooltip-arrow" /></Tooltip.Content>
    </Tooltip.Portal>
  </Tooltip.Root>;
}
