"use client";

import { V17Card } from "@/components/shared/page-filter-bar";
import { useApi } from "@/lib/hooks/use-api";

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

// 设计稿 L4784 renderPageSkill — 技能市场
export function SkillPage() {
  const { data } = useApi<SkillsEnvelope | null>("/api/v1/skills");
  const skills = Array.isArray(data?.skills) ? data!.skills! : [];

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card title="技能市场">
        {skills.length === 0 && (
          <div className="text-center text-ink-4 text-[11px] py-3">暂无数据</div>
        )}
        <div
          className="grid gap-2"
          style={{ gridTemplateColumns: "repeat(4, 1fr)" }}
        >
          {skills.map((s) => (
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
              <div
                className={`skill-toggle mt-2 inline-block rounded px-2 py-0.5 text-[9px] bg-surface-3 ${s.enabled ? "text-ink-3" : "text-ink-4"}`}
                title="状态展示(只读)"
              >
                {s.enabled ? "已启用" : "未启用"}
              </div>
            </div>
          ))}
        </div>
      </V17Card>
    </div>
  );
}
