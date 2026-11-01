"use client";

/**
 * 研究图谱只读视图(P2)—— "上一段历史改变下一次判断"看得见。
 *
 * 数据:/view 全图投影(question/revision/hypothesis/episode 节点 + 关系边)。
 * 关系由**服务端**给全 —— 边表 ∪ 列派生(revision.question_id /
 * evidence.episode_id),见 storage 的 relations.list_relations。
 * 本组件不再自己合成边:私补只在本页可见,别的消费者看不到同一张图。
 * 渲染:echarts graph 力导向,动态 import 防 SSR。
 * 高亮:?focus=<id>(investigate 后跳转带 revision id)→ 该节点放大
 * + 邻接节点/边提亮,一进页面就看见"这轮调查新连了什么"。
 */

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";

import { getGraphView, type GraphView } from "@/lib/api/research-graph";
import { SkeletonRows } from "@/components/shared/skeleton";
import { useUiText } from "@/components/i18n/use-ui-text"

const CATEGORY_COLOR: Record<string, string> = {
  question: "#5b8cff",
  revision: "#39c5a3",
  hypothesis: "#d9a441",
  episode: "#767a7e",
  mission: "#a78bfa",
};

const CATEGORY_LABEL: Record<string, string> = {
  question: "问题",
  revision: "回答",
  hypothesis: "假设",
  episode: "情景",
  mission: "使命",
};

// 节点分类顺序即 echarts category 索引,顺序不可随意改动(索引与颜色/图例绑定)
const CATEGORY_ORDER = ["question", "revision", "hypothesis", "episode", "mission"] as const;

// 过时假设(refuted/retired)在图上降透明 + 描边,区分"历史有效 ≠ 当前有效"(B3)
const SUPERSEDED_STATUSES = new Set(["refuted", "retired"]);

/** 点击节点的跳转目标;无对应详情页的类别返回 null(不可点) */
function nodeHref(node: GraphView["nodes"][number]): string | null {
  switch (node.category) {
    case "question":
      return `/research/questions?q=${encodeURIComponent(node.id)}`;
    case "mission":
      return `/research/missions?id=${encodeURIComponent(node.id)}`;
    case "episode":
      return `/research/episodes?id=${encodeURIComponent(node.id)}`;
    default:
      return null;
  }
}

export function GraphViewPanel() {
  const tr = useUiText()

  const params = useSearchParams();
  const focus = params.get("focus");
  // 图在 canvas 里,点节点只能走命令式导航;用 router.push 而非
  // window.location.assign —— 后者整页刷新,桌面壳里会闪一下白屏。
  const router = useRouter();

  const [view, setView] = useState<GraphView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const hostRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const v = await getGraphView();
        if (alive) setView(v);
      } catch {
        if (alive)
          setError("后端不可用 — 确认 local-api 已启动(localhost:8088)");
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    const host = hostRef.current;
    if (!host || !view) return;
    let disposed = false;
    let chart: { dispose: () => void } | null = null;

    import("echarts").then((echarts) => {
      if (disposed) return;

      // 关系一律来自服务端(边表 ∪ 列派生,见 relations.list_relations)。
      // 此前这里把 revision 的 question_id 合成为 belongs_to 边 —— 那会让
      // 前端私补的关系只在本页可见,`/view` 的其它消费者看不到同一张图。
      const links = view.links.map((l) => ({ ...l }));

      const neighbors = new Set<string>(focus ? [focus] : []);
      if (focus) {
        for (const l of links) {
          if (l.source === focus) neighbors.add(l.target);
          if (l.target === focus) neighbors.add(l.source);
        }
      }

      const chartInstance = echarts.init(host, "dark", { renderer: "canvas" });
      chart = chartInstance;

      // 点击节点跳转到其详情页(question/mission/episode 可下钻;revision/hypothesis 无详情页)
      chartInstance.on("click", (params) => {
        if (params.dataType !== "node") return;
        const data = params.data as { id?: string } | undefined;
        const node = view.nodes.find((n) => n.id === data?.id);
        if (!node) return;
        const href = nodeHref(node);
        if (href) router.push(href);
      });

      chartInstance.setOption({
        backgroundColor: "transparent",
        tooltip: { show: true },
        legend: [
          {
            data: [...CATEGORY_ORDER],
            formatter: (name: string) => CATEGORY_LABEL[name] ?? name,
            textStyle: { color: "#9aa0a6", fontSize: 10 },
            itemWidth: 12,
            itemHeight: 8,
            top: 4,
            left: 8,
          },
        ],
        series: [
          {
            type: "graph",
            layout: "force",
            roam: true,
            draggable: true,
            force: { repulsion: 160, edgeLength: 70, gravity: 0.08 },
            categories: CATEGORY_ORDER.map((name) => ({ name })),
            data: view.nodes.map((n) => {
              const isFocus = n.id === focus;
              const dimmed = Boolean(focus) && !neighbors.has(n.id);
              const superseded =
                n.category === "hypothesis" && SUPERSEDED_STATUSES.has(n.status ?? "");
              return {
                id: n.id,
                name: n.name || CATEGORY_LABEL[n.category],
                category: CATEGORY_ORDER.indexOf(n.category as (typeof CATEGORY_ORDER)[number]),
                symbolSize:
                  n.category === "question"
                    ? isFocus
                      ? 34
                      : 24
                    : n.category === "mission"
                      ? isFocus
                        ? 30
                        : 20
                      : isFocus
                        ? 26
                        : 16,
                itemStyle: {
                  color: CATEGORY_COLOR[n.category],
                  opacity: dimmed ? 0.22 : superseded ? 0.5 : 1,
                  borderColor: superseded ? "#767a7e" : isFocus ? "#ffffff" : "transparent",
                  borderWidth: superseded ? 1.5 : isFocus ? 2 : 0,
                  borderType: superseded ? "dashed" : "solid",
                },
                label: {
                  show: n.category === "question" || n.category === "mission" || isFocus,
                  color: "#c9ccd1",
                  fontSize: 10,
                },
              };
            }),
            links: links.map((l) => ({
              ...l,
              lineStyle: {
                color: focus && (l.source === focus || l.target === focus)
                  ? "#e8eaed"
                  : "#3a3f45",
                width: focus && (l.source === focus || l.target === focus) ? 1.6 : 0.8,
                curveness: 0.1,
              },
            })),
          },
        ],
      });
    });

    return () => {
      disposed = true;
      chart?.dispose();
    };
  }, [view, focus, router]);

  if (error) {
    return (
      <div className="mx-auto flex h-full max-w-2xl flex-col justify-center gap-2 p-6">
        <p className="text-xs text-destructive">{tr(error)}</p>
      </div>
    );
  }

  if (!view) {
    return (
      <div className="mx-auto h-full max-w-2xl p-6">
        <SkeletonRows rows={8} />
      </div>
    );
  }

  const focusNode = focus ? view.nodes.find((n) => n.id === focus) : null;

  return (
    <div className="flex h-full flex-col p-4">
      <div className="flex items-center gap-3">
        <h1 className="text-[13px] font-semibold text-ink">{tr("研究图谱")}</h1>
        <span className="text-[10px] text-ink-3">
          {view.nodes.length} {tr("节点 ·")} {view.links.length} {tr("边 — 点节点跳详情,拖拽调布局")}
        </span>
        {focusNode && (
          <span className="ml-auto flex items-center gap-2 rounded border border-accent/40 bg-accent/5 px-2 py-0.5 text-[10px] text-accent">
            {tr("高亮:")}{tr(CATEGORY_LABEL[focusNode.category])} {focusNode.name}
            <Link href="/research/graph" className="text-ink-3 hover:text-ink">
              ✕
            </Link>
          </span>
        )}
      </div>
      <div
        ref={hostRef}
        className="mt-2 flex-1 rounded-lg border border-border"
        style={{ minHeight: 480, background: "var(--surface-1)" }}
      />
    </div>
  );
}
