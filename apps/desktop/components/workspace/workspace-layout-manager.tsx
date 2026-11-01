"use client";

import { useRef, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  deleteLayout,
  duplicateLayout,
  exportLayoutToJSON,
  generateLayoutId,
  importLayoutFromJSON,
  listLayouts,
  renameLayout,
  saveLayout,
  type WorkspaceLayout,
} from "@/lib/workspace/workspace-layouts";
import { useUiText } from "@/components/i18n/use-ui-text"

interface Props {
  layouts: WorkspaceLayout[];
  onChange: () => void;
}

type DialogState =
  | { kind: "rename"; layout: WorkspaceLayout }
  | { kind: "delete"; layout: WorkspaceLayout }
  | { kind: "duplicate"; layout: WorkspaceLayout }
  | { kind: "save-current" }
  | { kind: "import-error"; message: string }
  | null;

/**
 * WorkspaceLayoutManager — 布局管理面板
 *
 * 在 /workspace 首页布局卡片上,每个卡片支持:
 * - 重命名
 * - 删除(带确认)
 * - 复制(生成新 id + 名称加 " 副本")
 * - 保存当前布局(从当前 workspace 页面导出)
 */
export function WorkspaceLayoutManager({ layouts, onChange }: Props) {
  const tr = useUiText()

  const [dialog, setDialog] = useState<DialogState>(null);
  const [nameInput, setNameInput] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);

  function openRename(layout: WorkspaceLayout) {
    setNameInput(layout.name);
    setDialog({ kind: "rename", layout });
  }
  function openDuplicate(layout: WorkspaceLayout) {
    setNameInput(tr("{name} 副本", { name: tr(layout.name) }));
    setDialog({ kind: "duplicate", layout });
  }
  function openDelete(layout: WorkspaceLayout) {
    setDialog({ kind: "delete", layout });
  }
  function openSaveCurrent() {
    setNameInput("");
    setDialog({ kind: "save-current" });
  }

  function closeDialog() {
    setDialog(null);
    setNameInput("");
  }

  function handleExport(layout: WorkspaceLayout) {
    const json = exportLayoutToJSON(layout);
    const blob = new Blob([json], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    // 文件名:保留中文/字母/数字/下划线/连字符,其他替换为 _
    const safeName = layout.name.replace(/[^\w一-龥-]/g, "_");
    a.download = `${safeName}.workspace.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  function handleImportClick() {
    fileInputRef.current?.click();
  }

  function handleImportFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      // input reset 在 read 完成后(避免 race:同步清空可能导致重选同文件不触发 change)
      e.target.value = "";
      const text = String(reader.result ?? "");
      const result = importLayoutFromJSON(text);
      if (result.ok) {
        saveLayout(result.ok);
        onChange();
      } else {
        // Dialog 替代 alert(与项目其他错误提示一致)
        setDialog({
          kind: "import-error",
          message: result.error ?? "未知错误",
        });
      }
    };
    reader.onerror = () => {
      e.target.value = "";
      setDialog({ kind: "import-error", message: "文件读取失败" });
    };
    reader.readAsText(file);
    // 不在这里同步清 e.target.value(race:FileReader 异步未完成时清空可能丢选择)
  }

  function handleConfirm() {
    if (!dialog) return;
    if (dialog.kind === "rename") {
      if (!nameInput.trim()) return;
      renameLayout(dialog.layout.id, nameInput.trim());
    } else if (dialog.kind === "delete") {
      deleteLayout(dialog.layout.id);
    } else if (dialog.kind === "duplicate") {
      if (!nameInput.trim()) return;
      duplicateLayout(dialog.layout.id, generateLayoutId(), nameInput.trim());
    } else if (dialog.kind === "save-current") {
      if (!nameInput.trim()) return;
      // 保存当前布局:从 localStorage 读 "current" 临时态,或用默认空布局
      // 简化:用户在 /workspace/[layoutId] 页面修改后,这里暂未实现拖拽编辑(P7),所以"保存当前"= 复制 signal 预设作为新布局
      const signalPreset = listLayouts().find((l) => l.id === "signal");
      if (signalPreset) {
        saveLayout({
          id: generateLayoutId(),
          name: nameInput.trim(),
          panels: signalPreset.panels,
          columns: signalPreset.columns,
          schemaVersion: 1,
          createdAt: new Date().toISOString(),
          updatedAt: new Date().toISOString(),
        });
      }
    }
    closeDialog();
    onChange();
  }

  return (
    <>
      <div className="mb-4 flex justify-end gap-2">
        <input
          ref={fileInputRef}
          type="file"
          accept="application/json,.json"
          className="hidden"
          onChange={handleImportFile}
        />
        <Button onClick={handleImportClick} variant="outline" size="sm">
          {tr("导入布局")}
        </Button>
        <Button onClick={openSaveCurrent} variant="outline" size="sm">
          {tr("保存当前布局")}
        </Button>
      </div>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
        {layouts.map((layout) => (
          <LayoutCard
            key={layout.id}
            layout={layout}
            onRename={() => openRename(layout)}
            onDuplicate={() => openDuplicate(layout)}
            onDelete={() => openDelete(layout)}
            onExport={() => handleExport(layout)}
          />
        ))}
      </div>

      <Dialog open={dialog !== null} onOpenChange={(open) => !open && closeDialog()}>
        <DialogContent className="max-w-md">
          {dialog?.kind === "rename" && (
            <>
              <DialogHeader>
                <DialogTitle>{tr("重命名布局")}</DialogTitle>
              </DialogHeader>
              <Input
                value={nameInput}
                onChange={(e) => setNameInput(e.target.value)}
                placeholder={tr("布局名称")}
                autoFocus
              />
              <DialogFooter>
                <Button variant="outline" onClick={closeDialog}>
                  {tr("取消")}
                </Button>
                <Button onClick={handleConfirm} disabled={!nameInput.trim()}>
                  {tr("确认")}
                </Button>
              </DialogFooter>
            </>
          )}
          {dialog?.kind === "duplicate" && (
            <>
              <DialogHeader>
                <DialogTitle>{tr("复制布局")}</DialogTitle>
              </DialogHeader>
              <Input
                value={nameInput}
                onChange={(e) => setNameInput(e.target.value)}
                placeholder={tr("新布局名称")}
                autoFocus
              />
              <DialogFooter>
                <Button variant="outline" onClick={closeDialog}>
                  {tr("取消")}
                </Button>
                <Button onClick={handleConfirm} disabled={!nameInput.trim()}>
                  {tr("复制")}
                </Button>
              </DialogFooter>
            </>
          )}
          {dialog?.kind === "delete" && (
            <>
              <DialogHeader>
                <DialogTitle>{tr("删除布局")}</DialogTitle>
              </DialogHeader>
              <p className="text-sm text-ink-3">
                {tr("确认删除布局「")}{tr(dialog.layout.name)}{tr("」?此操作不可撤销。")}
              </p>
              <DialogFooter>
                <Button variant="outline" onClick={closeDialog}>
                  {tr("取消")}
                </Button>
                <Button variant="destructive" onClick={handleConfirm}>
                  {tr("删除")}
                </Button>
              </DialogFooter>
            </>
          )}
          {dialog?.kind === "save-current" && (
            <>
              <DialogHeader>
                <DialogTitle>{tr("保存当前布局")}</DialogTitle>
              </DialogHeader>
              <p className="text-sm text-ink-4">
                {tr("将当前 workspace 布局保存为新布局(P7 支持拖拽编辑后,这里会保存当前实际配置)。")}
              </p>
              <Input
                value={nameInput}
                onChange={(e) => setNameInput(e.target.value)}
                placeholder={tr("新布局名称")}
                autoFocus
              />
              <DialogFooter>
                <Button variant="outline" onClick={closeDialog}>
                  {tr("取消")}
                </Button>
                <Button onClick={handleConfirm} disabled={!nameInput.trim()}>
                  {tr("保存")}
                </Button>
              </DialogFooter>
            </>
          )}
          {dialog?.kind === "import-error" && (
            <>
              <DialogHeader>
                <DialogTitle>{tr("导入失败")}</DialogTitle>
              </DialogHeader>
              <p className="text-sm text-ink-3">{dialog.message}</p>
              <DialogFooter>
                <Button variant="outline" onClick={closeDialog}>
                  {tr("知道了")}
                </Button>
              </DialogFooter>
            </>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}

interface LayoutCardProps {
  layout: WorkspaceLayout;
  onRename: () => void;
  onDuplicate: () => void;
  onDelete: () => void;
  onExport: () => void;
}

function LayoutCard({ layout, onRename, onDuplicate, onDelete, onExport }: LayoutCardProps) {
  const tr = useUiText()

  return (
    <div className="group relative rounded-xl border border-border bg-card p-5 transition-colors hover:border-ink-3">
      <a
        href={`/workspace/${layout.id}`}
        className="block"
      >
        <div className="flex items-center justify-between">
          <h3 className="font-medium text-ink-1 group-hover:text-ink-2">
            {tr(layout.name)}
          </h3>
          <span className="text-xs text-ink-4">{layout.panels.length} {tr("面板")}</span>
        </div>
        <div className="mt-3 flex flex-wrap gap-1.5">
          {layout.panels.map((p, idx) => (
            <span
              key={`${p.id}-${idx}`}
              className="rounded-md bg-bg-2 px-2 py-0.5 text-xs text-ink-3"
            >
              {p.id}
            </span>
          ))}
        </div>
        <div className="mt-3 text-xs text-ink-4">
          {tr("列数")} {layout.columns ?? 2} {tr("· 更新于")} {new Date(layout.updatedAt).toLocaleDateString()}
        </div>
      </a>
      <div className="mt-3 flex gap-2 border-t border-border pt-3">
        <button
          type="button"
          onClick={onRename}
          className="text-xs text-ink-3 hover:text-ink-1"
        >
          {tr("重命名")}
        </button>
        <span className="text-ink-4">·</span>
        <button
          type="button"
          onClick={onDuplicate}
          className="text-xs text-ink-3 hover:text-ink-1"
        >
          {tr("复制")}
        </button>
        <span className="text-ink-4">·</span>
        <button
          type="button"
          onClick={onExport}
          className="text-xs text-ink-3 hover:text-ink-1"
        >
          {tr("导出")}
        </button>
        <span className="text-ink-4">·</span>
        <button
          type="button"
          onClick={onDelete}
          className="text-xs text-ink-3 hover:text-destructive"
        >
          {tr("删除")}
        </button>
      </div>
    </div>
  );
}
