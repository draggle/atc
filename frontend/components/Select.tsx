"use client";

import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";

export interface SelectOption {
  value: string;
  label: string;
  /** A muted second line in the popover. The closed button never shows it, so the row stays narrow. */
  detail?: string;
  disabled?: boolean;
}

/**
 * The popover's own animation. Kept here rather than in globals.css so this component is one file:
 * the class name is namespaced, and the reduced-motion guard is part of it.
 */
const CSS = `
.sel-pop { transform-origin: top center; animation: sel-pop 0.22s cubic-bezier(0.22, 1, 0.36, 1) both; }
@keyframes sel-pop { from { opacity: 0; transform: translateY(-4px) scaleY(0.98); } to { opacity: 1; transform: none; } }
@media (prefers-reduced-motion: reduce) { .sel-pop { animation-duration: 0.01s; } }
`;

const Caret = () => (
  <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <path d="M6 9l6 6 6-6" />
  </svg>
);

interface Props {
  options: SelectOption[];
  value: string;
  onChange: (value: string) => void;
  /** Shown above the button, muted. Also the accessible name. */
  label?: string;
  placeholder?: string;
  disabled?: boolean;
  className?: string;
}

/**
 * A small flat dropdown: a button with the current label and a caret, opening a listbox anchored
 * under it. Focus never leaves the button; the active row is announced with aria-activedescendant,
 * so Escape has nothing to restore. Renders in flow with a high z-index, which is enough inside the
 * setup dialog (no portal).
 */
export default function Select({ options, value, onChange, label, placeholder = "Choose", disabled, className = "" }: Props) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const ref = useRef<HTMLDivElement | null>(null);
  const btnRef = useRef<HTMLButtonElement | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);
  // Fixed coordinates measured from the button. Fixed, not absolute, so the popover is never clipped
  // by the dialog's own overflow; nothing above it is transformed, so the viewport is the frame.
  const [box, setBox] = useState<{ left: number; width: number; top?: number; bottom?: number; maxHeight: number } | null>(null);

  const chosen = useMemo(() => options.find((o) => o.value === value), [options, value]);
  const selectedIndex = Math.max(0, options.findIndex((o) => o.value === value));

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  // Keep the active row in view when the list is long enough to scroll.
  useEffect(() => {
    if (!open) return;
    document.getElementById(`${id}-opt-${active}`)?.scrollIntoView({ block: "nearest" });
  }, [open, active, id]);

  const place = useCallback(() => {
    const r = btnRef.current?.getBoundingClientRect();
    if (!r) return;
    const gap = 4;
    const margin = 12;
    const below = window.innerHeight - r.bottom - gap - margin;
    const above = r.top - gap - margin;
    const wanted = Math.min(240, options.length * 42 + 8);
    const up = below < wanted && above > below;
    setBox({
      left: r.left,
      width: r.width,
      ...(up ? { bottom: window.innerHeight - r.top + gap } : { top: r.bottom + gap }),
      maxHeight: Math.max(96, Math.min(240, up ? above : below)),
    });
  }, [options.length]);

  const openWith = (index: number) => {
    setActive(index);
    place();
    setOpen(true);
  };

  // The dialog does not scroll, but a window resize would leave the popover behind.
  useEffect(() => {
    if (!open) return;
    const onResize = () => setOpen(false);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [open]);

  const step = (from: number, dir: 1 | -1) => {
    for (let i = 1; i <= options.length; i += 1) {
      const next = (from + dir * i + options.length * i) % options.length;
      if (!options[next]?.disabled) return next;
    }
    return from;
  };

  const pick = (index: number) => {
    const opt = options[index];
    if (!opt || opt.disabled) return;
    onChange(opt.value);
    setOpen(false);
    btnRef.current?.focus();
  };

  // Space is push-to-talk everywhere else on this screen (CommandBar listens on window), so every
  // key this dropdown handles is stopped here as well as default-prevented.
  const onKeyDown = (e: React.KeyboardEvent) => {
    if (disabled) return;
    if (!open) {
      if (e.key === "Enter" || e.key === " " || e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        e.stopPropagation();
        e.nativeEvent.stopImmediatePropagation();
        openWith(selectedIndex);
      }
      return;
    }
    if (["Escape", "ArrowDown", "ArrowUp", "Home", "End", "Enter", " "].includes(e.key)) {
      e.stopPropagation();
      e.nativeEvent.stopImmediatePropagation();
    }
    if (e.key === "Escape") {
      e.preventDefault();
      setOpen(false);
      btnRef.current?.focus();
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((a) => step(a, 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => step(a, -1));
    } else if (e.key === "Home") {
      e.preventDefault();
      setActive(step(options.length - 1, 1));
    } else if (e.key === "End") {
      e.preventDefault();
      setActive(step(0, -1));
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      pick(active);
    } else if (e.key === "Tab") {
      setOpen(false);
    }
  };

  return (
    <div className={`flex flex-col gap-1.5 ${className}`} ref={ref}>
      <style>{CSS}</style>
      {label && <span className="text-xs text-muted" id={`${id}-label`}>{label}</span>}
      <div className="relative">
        <button
          ref={btnRef}
          type="button"
          disabled={disabled || options.length === 0}
          onClick={() => (open ? setOpen(false) : openWith(selectedIndex))}
          onKeyDown={onKeyDown}
          aria-haspopup="listbox"
          aria-expanded={open}
          aria-labelledby={label ? `${id}-label ${id}-value` : undefined}
          aria-activedescendant={open ? `${id}-opt-${active}` : undefined}
          className="w-full flex items-center justify-between gap-2 h-9 px-3 border border-line rounded-[8px] bg-panel-2 text-left text-sm text-fg transition-colors hover:border-[#34373d] focus-visible:outline-none focus-visible:border-fg disabled:opacity-40 disabled:cursor-not-allowed"
        >
          <span id={`${id}-value`} className={`truncate ${chosen ? "text-fg" : "text-muted"}`}>{chosen?.label ?? placeholder}</span>
          <span className="text-muted shrink-0"><Caret /></span>
        </button>

        {open && (
          <div
            ref={listRef}
            role="listbox"
            aria-labelledby={label ? `${id}-label` : undefined}
            style={{ position: "fixed", left: box?.left, width: box?.width, top: box?.top, bottom: box?.bottom, maxHeight: box?.maxHeight, transformOrigin: box?.bottom !== undefined ? "bottom center" : "top center" }}
            className="sel-pop panel z-[60] overflow-y-auto scroll-thin py-1"
          >
            {options.map((o, i) => {
              const isSelected = o.value === value;
              return (
                <div
                  key={o.value}
                  id={`${id}-opt-${i}`}
                  role="option"
                  aria-selected={isSelected}
                  aria-disabled={o.disabled || undefined}
                  onMouseEnter={() => !o.disabled && setActive(i)}
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => pick(i)}
                  className={`flex items-start gap-2 px-3 py-1.5 cursor-pointer ${o.disabled ? "opacity-40 cursor-not-allowed" : ""} ${i === active && !o.disabled ? "bg-[#1e2024]" : ""}`}
                >
                  <span aria-hidden className={`mt-[7px] w-1.5 h-1.5 rounded-full shrink-0 ${isSelected ? "bg-fg" : "bg-transparent"}`} />
                  <span className="min-w-0">
                    <span className={`block text-sm truncate ${isSelected ? "text-fg" : "text-fg/90"}`}>{o.label}</span>
                    {o.detail && <span className="block text-xs text-muted truncate">{o.detail}</span>}
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
