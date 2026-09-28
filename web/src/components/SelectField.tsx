import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { Icon } from "./Icon";

export interface SelectOption {
  value: string;
  label: string;
}

interface SelectFieldProps {
  id: string;
  label: string;
  name: string;
  onChange: (value: string) => void;
  options: readonly SelectOption[];
  value: string;
  descriptionId?: string;
  compact?: boolean;
}

interface MenuPosition {
  left: number;
  top: number;
  width: number;
}

export function SelectField({ id, label, name, onChange, options, value, descriptionId, compact = false }: SelectFieldProps) {
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState<MenuPosition | null>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const optionButtons = useRef<(HTMLButtonElement | null)[]>([]);
  const firstFocus = useRef(0);
  const selected = options.find((option) => option.value === value);

  function showMenu(index = Math.max(0, options.findIndex((option) => option.value === value))) {
    if (options.length === 0) return;
    const bounds = trigger.current?.getBoundingClientRect();
    if (!bounds) return;
    const height = Math.min(options.length * 40 + 8, 256);
    const above = window.innerHeight - bounds.bottom < height + 12 && bounds.top > height + 12;
    const top = above ? bounds.top - height - 4 : bounds.bottom + 4;
    setPosition({
      left: Math.max(12, Math.min(bounds.left, window.innerWidth - bounds.width - 12)),
      top: Math.max(12, Math.min(top, window.innerHeight - height - 12)),
      width: Math.min(bounds.width, window.innerWidth - 24),
    });
    firstFocus.current = index;
    setOpen(true);
  }

  useEffect(() => {
    if (!open) return;
    optionButtons.current[firstFocus.current]?.focus({ preventScroll: true });
    const dismiss = (event: PointerEvent) => {
      if (!trigger.current?.contains(event.target as Node) && !menu.current?.contains(event.target as Node)) setOpen(false);
    };
    const reposition = (event: Event) => {
      if (event.type === "scroll" && menu.current?.contains(event.target as Node)) return;
      setOpen(false);
    };
    document.addEventListener("pointerdown", dismiss);
    window.addEventListener("resize", reposition);
    window.addEventListener("scroll", reposition, true);
    return () => {
      document.removeEventListener("pointerdown", dismiss);
      window.removeEventListener("resize", reposition);
      window.removeEventListener("scroll", reposition, true);
    };
  }, [open]);

  function choose(next: string) {
    onChange(next);
    setOpen(false);
    trigger.current?.focus();
  }

  function moveFocusAfterMenu(backwards: boolean) {
    if (!trigger.current) { setOpen(false); return; }
    const focusable = Array.from(document.querySelectorAll<HTMLElement>(
      'a[href], button:not([disabled]), input:not([type="hidden"]):not([disabled]), select:not([disabled]), summary, textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    )).filter((element) => !menu.current?.contains(element) && element.getClientRects().length > 0);
    const current = focusable.indexOf(trigger.current);
    const next = focusable[current + (backwards ? -1 : 1)];
    setOpen(false);
    (next ?? trigger.current).focus();
  }

  function onMenuKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    const index = optionButtons.current.findIndex((button) => button === document.activeElement);
    let next = index;
    if (event.key === "ArrowDown") next = (index + 1) % options.length;
    else if (event.key === "ArrowUp") next = (index - 1 + options.length) % options.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = options.length - 1;
    else if (event.key === "Escape") { event.preventDefault(); setOpen(false); trigger.current?.focus(); return; }
    else if (event.key === "Tab") { event.preventDefault(); moveFocusAfterMenu(event.shiftKey); return; }
    else if (event.key.length === 1) {
      const query = event.key.toLocaleLowerCase();
      const match = options.findIndex((option, offset) => offset > index && option.label.toLocaleLowerCase().startsWith(query));
      next = match >= 0 ? match : options.findIndex((option) => option.label.toLocaleLowerCase().startsWith(query));
    } else return;
    event.preventDefault();
    optionButtons.current[next]?.focus();
  }

  const popup = open && position ? <div
    aria-labelledby={`${id}-label`} className="select-menu__popup" id={`${id}-menu`}
    onKeyDown={onMenuKeyDown} ref={menu} role="menu"
    style={{ left: position.left, top: position.top, width: position.width }}
  >{options.map((option, index) => <button
    aria-checked={option.value === value} className="select-menu__option" key={option.value}
    onClick={() => choose(option.value)} ref={(node) => { optionButtons.current[index] = node; }}
    role="menuitemradio" type="button"
  ><span>{option.label}</span>{option.value === value ? <Icon name="check" /> : null}</button>)}</div> : null;

  return <>
    <div className={`select-field${compact ? " select-field--compact" : ""}`}>
      <label className={`select-field__label${compact ? " visually-hidden" : ""}`} htmlFor={id} id={`${id}-label`}>{label}</label>
      <button
        aria-controls={open ? `${id}-menu` : undefined} aria-describedby={descriptionId}
        aria-expanded={open} aria-haspopup="menu" aria-labelledby={`${id}-label ${id}-value`}
        className="select-field__trigger" disabled={options.length === 0} id={id} onClick={() => open ? setOpen(false) : showMenu()} ref={trigger}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault(); showMenu(event.key === "ArrowDown" ? 0 : options.length - 1);
          } else if (event.key === "Escape" && open) { event.preventDefault(); setOpen(false); }
        }} type="button"
      ><span id={`${id}-value`}>{selected?.label ?? "请选择"}</span><Icon name="chevronDown" /></button>
      <input name={name} type="hidden" value={value} />
    </div>
    {popup ? createPortal(popup, trigger.current?.closest("dialog") ?? document.body) : null}
  </>;
}
