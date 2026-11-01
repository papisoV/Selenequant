"use client";

import type { ReactNode } from "react";
import { clickable } from "@/lib/a11y/clickable";
import { useUiText } from "@/components/i18n/use-ui-text"

interface Props {
  show: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
}

export function DrawerPanel({ show, title, onClose, children }: Props) {
  const tr = useUiText()

  return (
    <div className={`drawer-panel left ${show ? "show" : ""}`}>
      <div className="dp-head">
        <span className="dp-title">{title}</span>
        <div className="dp-close" aria-label={tr("关闭")} title={tr("关闭")} {...clickable(onClose)}>
          <svg viewBox="0 0 12 12" fill="none" width="12" height="12">
            <path d="M2 2 L10 10 M10 2 L2 10" stroke="currentColor" strokeWidth="1.4" />
          </svg>
        </div>
      </div>
      <div className="dp-body">{children}</div>
    </div>
  );
}
