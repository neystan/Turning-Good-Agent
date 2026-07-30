import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import { api } from "../api";
import inspectIcon from "../assets/slash-icons/inspect.svg";
import mcpIcon from "../assets/slash-icons/mcp.svg";
import skillIcon from "../assets/slash-icons/skill.svg";
import toolsIcon from "../assets/slash-icons/tools.svg";
import { ScrollArea } from "./ScrollArea";
import type { CommandEntry } from "../types";

export function SlashCommandMenu({ slashToken, onSelect }: { slashToken: string | null; onSelect: (entry: CommandEntry) => void }) {
  const [entries, setEntries] = useState<CommandEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [activeIndex, setActiveIndex] = useState(0);
  const [dismissedDraft, setDismissedDraft] = useState<string | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const visible = slashToken !== null;

  useEffect(() => {
    if (!visible) {
      setEntries(null);
      setError(null);
      return;
    }
    let active = true;
    void api.commands().then((catalog) => {
      if (active) setEntries(catalog.entries);
    }).catch((reason: unknown) => {
      if (active) setError(reason instanceof Error ? reason.message : "命令目录不可用");
    });
    return () => { active = false; };
  }, [visible]);

  const filtered = useMemo(() => {
    const query = slashToken?.slice(1).toLowerCase() || "";
    return entries?.filter((entry) => `${entry.slash} ${entry.label} ${entry.description}`.toLowerCase().includes(query)) || [];
  }, [entries, slashToken]);
  const panelVisible = visible && dismissedDraft !== slashToken && (!entries || Boolean(error) || filtered.length > 0);

  useEffect(() => {
    if (!visible) {
      setActiveIndex(0);
      setDismissedDraft(null);
      return;
    }
    setActiveIndex(0);
    if (dismissedDraft !== null && dismissedDraft !== slashToken) setDismissedDraft(null);
  }, [entries, dismissedDraft, slashToken, visible]);

  useLayoutEffect(() => {
    if (!panelVisible || !filtered.length) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (!(document.activeElement instanceof HTMLElement) || document.activeElement.getAttribute("aria-label") !== "消息内容") return;
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setActiveIndex((index) => (index + 1) % filtered.length);
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        setActiveIndex((index) => (index - 1 + filtered.length) % filtered.length);
        return;
      }
      if (event.key === "Enter") {
        event.preventDefault();
        onSelect(filtered[activeIndex]);
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        setDismissedDraft(slashToken);
      }
    };
    document.addEventListener("keydown", onKeyDown, true);
    return () => document.removeEventListener("keydown", onKeyDown, true);
  }, [activeIndex, filtered, onSelect, panelVisible, slashToken]);

  useLayoutEffect(() => {
    const menu = menuRef.current;
    const activeOption = menu?.querySelector<HTMLElement>('button[aria-selected="true"]');
    if (!menu || !activeOption) return;

    const optionTop = activeOption.offsetTop;
    const optionBottom = optionTop + activeOption.offsetHeight;
    const viewportTop = menu.scrollTop;
    const viewportBottom = viewportTop + menu.clientHeight;

    if (optionTop < viewportTop) menu.scrollTop = optionTop;
    else if (optionBottom > viewportBottom) menu.scrollTop = optionBottom - menu.clientHeight;
  }, [activeIndex, panelVisible]);

  useEffect(() => {
    if (!panelVisible) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!(event.target instanceof Element) || event.target.closest(".composer")) return;
      setDismissedDraft(slashToken);
    };
    document.addEventListener("pointerdown", onPointerDown, true);
    return () => document.removeEventListener("pointerdown", onPointerDown, true);
  }, [panelVisible, slashToken]);

  if (!panelVisible) return null;
  return <ScrollArea viewportRef={menuRef} className="slash-command-menu" role="listbox" aria-label="Slash 命令" aria-activedescendant={filtered[activeIndex] ? `slash-command-${filtered[activeIndex].id}` : undefined}>{error && <p>{error}</p>}{!entries && !error && <p>正在读取命令…</p>}{entries && filtered.map((entry, index) => <button id={`slash-command-${entry.id}`} type="button" role="option" aria-label={entry.label} aria-selected={index === activeIndex} key={entry.id} onMouseEnter={() => setActiveIndex(index)} onMouseDown={(event) => event.preventDefault()} onClick={() => onSelect(entry)}><CommandIcon icon={entry.icon} /><strong>{entry.slash.slice(1)}</strong><small className="slash-command-summary">{entry.description}</small></button>)}</ScrollArea>;
}

const commandIconAssets: Record<CommandEntry["icon"], string> = {
  context: inspectIcon,
  mcp: mcpIcon,
  skill: skillIcon,
  tools: toolsIcon,
};

function CommandIcon({ icon }: { icon: CommandEntry["icon"] }) {
  const source = commandIconAssets[icon] || inspectIcon;
  return <span className="slash-command-icon" style={{ WebkitMaskImage: `url("${source}")`, maskImage: `url("${source}")` }} aria-hidden="true" />;
}
