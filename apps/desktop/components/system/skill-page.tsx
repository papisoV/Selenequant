"use client";

import { useState } from "react";
import { V17Card } from "@/components/shared/page-filter-bar";
import { fetchBare } from "@/lib/api-client";
import { useApi } from "@/lib/hooks/use-api";
import { useUiText } from "@/components/i18n/use-ui-text"

// 后端 /api/v1/skills 返回 { skills: [...], count }(_skill_to_dict — skills_api.py:121)
// 字段: name / description / source / tags / user_invocable / model_invocable /
//        requires_tools / file_path / enabled(无 category / installs 键)
interface BackendSkill {
  name?: string;
  description?: string;
  source?: string;
  tags?: string[];
  enabled?: boolean;
}

interface SkillsEnvelope {
  skills?: BackendSkill[];
  count?: number;
}

// PUT /skills/{name}/enable 返回裸 {name, enabled}(无 envelope):必须走
// fetchBare —— api()/apiPut 当 envelope 解 .data,裸 dict 会解成 null。
async function setSkillEnabled(name: string, enabled: boolean): Promise<void> {
  await fetchBare(`/api/v1/skills/${encodeURIComponent(name)}/enable`, {
    method: "PUT",
    body: JSON.stringify({ enabled }),
  });
}

// 设计稿 L4784 renderPageSkill — 技能市场
export function SkillPage() {
  const tr = useUiText()
  const { data, mutate } = useApi<SkillsEnvelope | null>("/api/v1/skills");
  const skills = Array.isArray(data?.skills) ? data!.skills! : [];
  // 本地覆盖:PUT 成功后即刻反映,失败回滚(服务端状态仍是真值,重拉会修正)。
  const [overrides, setOverrides] = useState<Record<string, boolean>>({});
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const toggle = async (name: string, next: boolean) => {
    setPending(name);
    setError(null);
    setOverrides((prev) => ({ ...prev, [name]: next }));
    try {
      await setSkillEnabled(name, next);
      void mutate?.();
    } catch (e) {
      setOverrides((prev) => {
        const copy = { ...prev };
        delete copy[name];
        return copy;
      });
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setPending(null);
    }
  };

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card title={tr("技能市场")}>
        {error && (
          <div className="mb-2 rounded border border-destructive/40 bg-destructive/10 px-2 py-1 text-[10px] text-destructive">
            {tr("启用状态保存失败")}:{error}
          </div>
        )}
        {skills.length === 0 && (
          <div className="text-center text-ink-4 text-[11px] py-3">{tr("暂无数据")}</div>
        )}
        <div
          className="grid gap-2"
          style={{ gridTemplateColumns: "repeat(4, 1fr)" }}
        >
          {skills.map((s) => {
            const name = s.name ?? "";
            const enabled = overrides[name] ?? s.enabled === true;
            const busy = pending === name;
            return (
              <div
                key={s.name}
                className="skill-card rounded border border-border-soft bg-surface-2 p-2"
              >
                <div className="skill-name text-[10px] font-medium text-ink">{s.name ?? "?"}</div>
                <div className="skill-cat text-[9px] text-gold mt-0.5">
                  {s.tags && s.tags.length > 0 ? s.tags.slice(0, 3).join(" · ") : s.source ?? "—"}
                </div>
                <div className="skill-desc text-[9px] text-ink-4 mt-1 line-clamp-2">
                  {s.description ?? "—"}
                </div>
                <button
                  type="button"
                  className={`skill-toggle mt-2 inline-block rounded px-2 py-0.5 text-[9px] bg-surface-3 ${enabled ? "text-ink-3" : "text-ink-4"} ${busy ? "opacity-50" : "hover:bg-surface-4"}`}
                  title={enabled ? tr("点击停用该技能") : tr("点击启用该技能")}
                  aria-label={`${enabled ? tr("停用") : tr("启用")}技能 ${name}`}
                  aria-pressed={enabled}
                  disabled={busy || !name}
                  onClick={() => void toggle(name, !enabled)}
                >
                  {busy ? tr("保存中…") : enabled ? tr("已启用") : tr("未启用")}
                </button>
              </div>
            );
          })}
        </div>
      </V17Card>
    </div>
  );
}
