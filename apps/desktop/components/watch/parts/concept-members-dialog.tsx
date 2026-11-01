"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { useUiText } from "@/components/i18n/use-ui-text";
import { useApiPage } from "@/lib/hooks/use-api-page";
import { freshnessBadge } from "./ashare-freshness";

/**
 * 概念成分下钻 —— 概念 RPS 行点开后,该概念的全部成员 + 行情。
 *
 * ## 为什么必须虚拟化
 *
 * 实测(2026-10-25 真实成分表):最大的概念 3,888 只,>1000 的有 9 个,
 * >200 的 107 个,中位数只有 20 只。中位数小不代表可以不做 —— 用户点开的
 * 恰恰是排在最前面的大概念,3,888 个行节点一次性挂载会让弹窗开卡数秒。
 *
 * ## 截断必须说出来
 *
 * 后端默认 `limit=1000` 并另报 `total`。这里把「共 3888 只,显示前 1000」
 * 直接印在标题栏:静默截断会让读者把一页切片当成整个概念 —— 那是这类面板
 * 最容易犯、也最不容易被发现的错。
 *
 * ## 价格口径要说清
 *
 * `quote_source` 区分实时快照与日线回退。收盘后快照源停止,`as_of` 是
 * **上一个交易日**,此时页面绝不能暗示「实时」。
 */

interface ConceptMemberRow {
  symbol: string;
  name: string;
  last: number | null;
  change_pct: number | null;
  amount: number | null;
  priced: boolean;
}

interface ConceptMembersData {
  status: "ok" | "empty" | "degraded";
  reason: string | null;
  concept: string;
  limit: number;
  /**
   * 以下字段**只在 status === "ok"** 时存在。降级分支(database 挂了/成分库空)
   * 只回 base(concept/limit/membership_note)+ status + reason ——
   * 见 `services/local-api/app/api/v1/ashare_concept.py:279-298`。
   * 标成必有会让消费端把「缺失」静默读成「0」。
   */
  total?: number;
  returned?: number;
  truncated?: boolean;
  rows?: ConceptMemberRow[];
  quote_source?: "snapshot" | "daily" | "none";
  quote_reason?: string | null;
  as_of?: string | null;
  priced_count?: number;
  membership_note: string;
}

type SortMode = "change_desc" | "change_asc" | "amount_desc" | "name";

const ROW_HEIGHT = 30;
/** 虚拟列表的兜底窗口尺寸:jsdom 量不出布局,SSR 首帧也量不出。 */
const INITIAL_RECT = { width: 640, height: 480 };

const fmtNum = (v: number | null, digits = 2) =>
  v === null || !Number.isFinite(v) ? "—" : v.toFixed(digits);

const fmtAmount = (v: number | null) => {
  if (v === null || !Number.isFinite(v)) return "—";
  if (Math.abs(v) >= 1e8) return `${(v / 1e8).toFixed(2)}亿`;
  if (Math.abs(v) >= 1e4) return `${(v / 1e4).toFixed(0)}万`;
  return v.toFixed(0);
};

/** A 股涨跌色:正=红、负=绿。与看盘台 `chgClass` 同一口径,不复用加密的 pos/neg。 */
const chgClass = (v: number | null): string => {
  if (v === null || !Number.isFinite(v) || v === 0) return "text-ink-3";
  return v > 0 ? "text-red-400" : "text-emerald-400";
};

const numOrNull = (v: number | null): number | null =>
  v === null || !Number.isFinite(v) ? null : v;

/**
 * 「成分表取不到」的单一渲染器 —— HTTP 错误与后端 degraded 共用。
 *
 * 合并的动因是能力对等:先前两条分支各写一遍,错误分支有重试、降级分支没有,
 * 于是瞬时故障在降级路径上只能靠刷新整页恢复。
 *
 * `role="alert"` 让读屏用户在异步跳变时被告知,而不是听到一片沉默。
 */
function Unavailable({
  message,
  onRetry,
  tr,
}: {
  message: string;
  onRetry: () => void;
  tr: (source: string, params?: Record<string, string | number>) => string;
}) {
  return (
    <p role="alert" className="py-6 text-center text-xs text-destructive">
      {tr("成分表暂不可用:{p0}", { p0: message })}{" "}
      <button
        type="button"
        onClick={onRetry}
        className="underline underline-offset-2 hover:text-ink-2"
      >
        {tr("重试")}
      </button>
    </p>
  );
}

/**
 * 降级原因 → 用户话术。**绝不把 reason 原样印到界面上**:那是给日志和监控的枚举值。
 * 未登记的原因走通用句 —— 宁可少说,也不能把机器码当文案。
 */
const DEGRADED_TEXT: Record<string, string> = {
  concept_membership_unavailable: "成分数据暂时取不到,请稍后重试",
  concept_membership_empty: "成分库尚未加载完成,请稍后重试",
};
const DEGRADED_FALLBACK = "成分数据暂时不可用,请稍后重试";

/**
 * 弹窗**内容**(不含 `<Dialog>` Root)。
 *
 * 分成两层是为了让触发器能持有 Root:Radix 的 DialogTrigger 必须在 Dialog 内,
 * 而关闭时的焦点归还依赖它填充 triggerRef(见 `ConceptMembersTrigger`)。
 */
function ConceptMembersPanel({
  concept,
  open,
  sessionToday,
  expectedDailySession,
}: {
  concept: string | null;
  open: boolean;
  /**
   * 新鲜度基准,由调用方从 `/session` 传入(与看盘台同一口径)。
   * `sessionToday` 用于盘中快照,`expectedDailySession` 用于日线回退 ——
   * 两者不可互换:日线盘中本来就停在上一交易日,拿今天当基准会每交易日
   * 9:30–15:00 常亮金色。
   *
   * 注意 `expectedDailySession` 至今**无生产调用方**:它依赖 `/session` 的
   * `expected_daily_session`,该字段随看盘台新鲜度基准那条线一起落地。在它
   * 接通之前,日线回退分支的 `freshnessBadge` 拿到 `null` ⇒ 按契约返回 `null`
   * (不标陈旧),即「静默但不错」;接通后自动生效,无需再改这里。
   */
  sessionToday?: string | null;
  expectedDailySession?: string | null;
}) {
  const tr = useUiText();
  const [search, setSearch] = useState("");
  const [sortMode, setSortMode] = useState<SortMode>("change_desc");

  // 弹窗关闭时不发请求:成分表按天变,价格随看盘台刷新,这里不做轮询。
  const path =
    open && concept
      ? `/api/v1/ashare/concept-members?concept=${encodeURIComponent(concept)}&limit=1000`
      : null;
  const { useApi } = useApiPage({ distinguish: true });
  const { data, error, isLoading, mutate } = useApi<ConceptMembersData>(path);

  // 注意用 undefined 而非 []:降级响应根本没有 rows,兜底成空数组会让下游
  // 把「字段缺失」当成「统计出来是 0」。
  const rows = data?.rows;

  const visibleRows = useMemo(() => {
    const keyword = search.trim().toLowerCase();
    const source = rows ?? [];
    const filtered = keyword
      ? source.filter((r) =>
          `${r.symbol} ${r.name}`.toLowerCase().includes(keyword),
        )
      : source;
    // 服务端已按涨幅降序;其余排序在客户端做(上限 1000 行,足够快)。
    const sorted = [...filtered];
    sorted.sort((a, b) => {
      if (sortMode === "name") {
        return (a.name || a.symbol).localeCompare(b.name || b.symbol, "zh-CN");
      }
      if (sortMode === "amount_desc") {
        return (numOrNull(b.amount) ?? -Infinity) - (numOrNull(a.amount) ?? -Infinity);
      }
      const av = numOrNull(a.change_pct);
      const bv = numOrNull(b.change_pct);
      if (av === null && bv === null) return a.symbol.localeCompare(b.symbol);
      // 无价的行恒排最后,与后端一致 —— 不能让「没取到价」混进涨跌榜中间。
      if (av === null) return 1;
      if (bv === null) return -1;
      return sortMode === "change_desc" ? bv - av : av - bv;
    });
    return sorted;
  }, [rows, search, sortMode]);

  /**
   * 统计只对**真实存在**的行计算。没有 rows(降级/加载中)时返回 null,
   * 渲染层整块隐藏 —— 不能退回 0/0/0:在看盘台上「涨 0 跌 0 平 0」是一个
   * 合法的市场状态读数(全线停牌),读不出「没取到数」。
   */
  const stats = useMemo(() => {
    if (!rows) return null;
    const changes = rows
      .map((r) => r.change_pct)
      .filter((v): v is number => v !== null && Number.isFinite(v));
    return {
      up: changes.filter((v) => v > 0).length,
      down: changes.filter((v) => v < 0).length,
      flat: changes.filter((v) => v === 0).length,
      unpriced: rows.length - changes.length,
    };
  }, [rows]);

  const scrollRef = useRef<HTMLDivElement>(null);
  const virtualizer = useVirtualizer({
    count: visibleRows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 10,
    getItemKey: (i) => visibleRows[i]?.symbol ?? i,
    initialRect: INITIAL_RECT,
  });

  // 过滤/换排序后回顶。滚到底再看搜索结果的用户,筛选后列表会短很多,
  // 而 scrollTop 仍停在旧位置 ⇒ 虚拟窗口落在空区间,看起来像「一条都没有」。
  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = 0;
  }, [search, sortMode]);

  // 关窗即清筛选。弹窗首次打开后一直挂着(见 ConceptMembersTrigger 注释),
  // 搜索词会活到下一次打开 —— 用户重开看到空表、标题却仍写着「共 3888 只」。
  // 排序是偏好不是查询,保留。
  useEffect(() => {
    if (!open) setSearch("");
  }, [open]);

  const sourceLabel =
    data?.quote_source === "snapshot"
      ? tr("实时快照")
      : data?.quote_source === "daily"
        ? tr("日线回退")
        : tr("无行情");

  // 陈旧度走屋子里的契约(ashare-freshness):新鲜不标、旧了金色、有数无日期
  // 标「无时标」。基准按数据源选 —— 快照对今天,日线对日线底座应到的交易日。
  const freshness =
    data?.quote_source === "snapshot"
      ? freshnessBadge(data.as_of, sessionToday ?? null)
      : data?.quote_source === "daily"
        ? freshnessBadge(data.as_of, expectedDailySession ?? null)
        : null;

  return (
    <DialogContent
      closeLabel={tr("关闭")}
      className="flex h-[86vh] max-h-[760px] w-[94vw] max-w-3xl flex-col gap-0 overflow-hidden p-0"
    >
        <DialogHeader className="shrink-0 border-b border-border px-4 py-3 pr-10 text-left">
          <DialogTitle className="text-sm">
            {concept ? tr("{concept} · 成分股", { concept }) : tr("成分股")}
          </DialogTitle>
          {/* 计数句只在后端真的给了 total 时渲染 —— 降级响应没有这个字段,
              自拼会印出字面 `共 {total} 只`(formatMessage 对缺失键回显花括号)。 */}
          <DialogDescription className="text-xs text-ink-4">
            {!data
              ? tr("加载中…")
              : data.total == null
                ? ""
                : data.truncated && data.returned != null
                  ? tr("共 {total} 只,显示前 {shown} 只", {
                      total: data.total,
                      shown: data.returned,
                    })
                  : tr("共 {total} 只", { total: data.total })}
          </DialogDescription>
        </DialogHeader>

        <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-border px-4 py-2">
          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={tr("搜索代码或名称")}
            aria-label={tr("搜索代码或名称")}
            className="h-7 w-40 rounded-xs border border-border bg-surface px-2 text-xs text-ink outline-none focus:border-ink-4"
          />
          <select
            value={sortMode}
            onChange={(e) => setSortMode(e.target.value as SortMode)}
            aria-label={tr("排序")}
            className="h-7 rounded-xs border border-border bg-surface px-1 text-xs text-ink"
          >
            <option value="change_desc">{tr("涨幅降序")}</option>
            <option value="change_asc">{tr("涨幅升序")}</option>
            <option value="amount_desc">{tr("成交额降序")}</option>
            <option value="name">{tr("名称")}</option>
          </select>
          {stats ? (
            <span className="text-xs text-ink-4" data-testid="member-stats">
              {tr("涨 {up} · 跌 {down} · 平 {flat}", stats)}
              {stats.unpriced > 0 ? tr(" · 无价 {n}", { n: stats.unpriced }) : null}
            </span>
          ) : null}
          <span
            className="ml-auto text-xs text-ink-4"
            data-testid="quote-source"
            title={data?.quote_reason ?? undefined}
          >
            {sourceLabel}
            {data?.as_of ? ` · ${data.as_of}` : ""}
            {freshness ? (
              <span className={freshness.cls}> {freshness.text}</span>
            ) : null}
          </span>
        </div>

        <div className="min-h-0 flex-1 overflow-hidden">
          {/* 两种「取不到表」合并成一个渲染器:HTTP 错误与后端 degraded 都要有
              重试出口。成分缓存是 fail-open 的瞬时故障,没有重试就只能刷整页。 */}
          {error && !data ? (
            <Unavailable
              message={error.message}
              onRetry={() => mutate()}
              tr={tr}
            />
          ) : isLoading && !data ? (
            // 骨架而不是居中文本:表就要来了,形状已知(行高 ROW_HEIGHT),
            // 让占位先把版面撑出来(DESIGN.md §8.6「加载用 skeleton」)。
            <div
              role="status"
              aria-busy="true"
              aria-label={tr("加载中…")}
              className="px-4 py-2"
              data-testid="member-skeleton"
            >
              {Array.from({ length: 6 }, (_, i) => (
                <div
                  key={i}
                  className="grid grid-cols-[1fr_5rem_5rem_5rem] items-center gap-2 border-b border-border-soft"
                  style={{ height: ROW_HEIGHT }}
                >
                  <span className="h-2 w-2/3 rounded-xs bg-surface-2" />
                  <span className="h-2 w-full rounded-xs bg-surface-2" />
                  <span className="h-2 w-full rounded-xs bg-surface-2" />
                  <span className="h-2 w-full rounded-xs bg-surface-2" />
                </div>
              ))}
            </div>
          ) : data?.status === "degraded" ? (
            <Unavailable
              message={
                DEGRADED_TEXT[data.reason ?? ""] ?? DEGRADED_FALLBACK
              }
              onRetry={() => mutate()}
              tr={tr}
            />
          ) : !visibleRows.length ? (
            <p className="py-6 text-center text-xs text-ink-4">
              {search ? tr("没有匹配的成分股") : tr("该概念暂无成分股")}
            </p>
          ) : (
            <div
              ref={scrollRef}
              className="h-full overflow-auto"
              data-testid="member-scroll"
              role="region"
              aria-label={tr("成分股列表")}
              tabIndex={0}
            >
              {/* 表头留在滚动容器内并 sticky:虚拟列表只接管 tbody 那一段。 */}
              <div className="sticky top-0 z-10 grid grid-cols-[1fr_5rem_5rem_5rem] gap-2 border-b border-border bg-surface-2 px-4 py-1 text-2xs text-ink-4">
                <span>{tr("代码 / 名称")}</span>
                <span className="text-right">{tr("现价")}</span>
                <span className="text-right">{tr("涨跌幅")}</span>
                <span className="text-right">{tr("成交额")}</span>
              </div>
              <div
                style={{ height: virtualizer.getTotalSize(), position: "relative" }}
              >
                {virtualizer.getVirtualItems().map((item) => {
                  const row = visibleRows[item.index];
                  if (!row) return null;
                  return (
                    <div
                      key={item.key}
                      data-testid="member-row"
                      className="absolute left-0 top-0 grid w-full grid-cols-[1fr_5rem_5rem_5rem] items-center gap-2 border-b border-border-soft px-4 font-mono text-xs"
                      style={{
                        height: item.size,
                        transform: `translateY(${item.start}px)`,
                      }}
                    >
                      <span className="truncate">
                        <span className="text-ink">{row.symbol}</span>
                        {row.name ? (
                          <span className="ml-1 text-ink-3">{row.name}</span>
                        ) : null}
                      </span>
                      <span className="text-right text-ink-2">
                        {fmtNum(row.last)}
                      </span>
                      <span className={`text-right ${chgClass(row.change_pct)}`}>
                        {fmtNum(row.change_pct)}
                      </span>
                      <span className="text-right text-ink-3">
                        {fmtAmount(row.amount)}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>

        {data?.membership_note ? (
          <p className="shrink-0 border-t border-border px-4 py-1 text-xs text-ink-4">
            {data.membership_note}
          </p>
        ) : null}
    </DialogContent>
  );
}

/**
 * 受控版本:调用方自行管理 open(测试与将来的内联场景用)。
 *
 * 生产路径走 `ConceptMembersTrigger` —— 它必须自己持有 `<Dialog>` Root,
 * 这样 `DialogTrigger` 才能填充 Radix 的 triggerRef,关闭时焦点才回得来。
 */
export function ConceptMembersDialog({
  concept,
  open,
  onOpenChange,
  sessionToday,
  expectedDailySession,
}: {
  concept: string | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  sessionToday?: string | null;
  expectedDailySession?: string | null;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <ConceptMembersPanel
        concept={concept}
        open={open}
        sessionToday={sessionToday}
        expectedDailySession={expectedDailySession}
      />
    </Dialog>
  );
}

/**
 * 概念名 → 可点开下钻的触发器。
 *
 * 把 state 收进这里,调用处只改一行:看盘台那份文件有多车道未提交改动,
 * 把 3 行改成 1 行能把冲突面压到最小。
 *
 * **首次打开前不挂载 Dialog**:看盘台一屏有 20 行概念,每行都挂一个关闭态的
 * 弹窗会让 20 个 `useApi(null)` 一起进 SWR —— 不是请求,但会在「每个渲染的
 * 端点都必须登记轮询档位」那条全页闸门里冒出一串空键。首次打开后保持挂载,
 * 这样关窗动画仍在。
 *
 * **Root 必须在这里**:Radix 关闭时执行 `context.triggerRef.current?.focus()`,
 * 而 triggerRef 只由 `DialogTrigger` 填充。若把 Root 藏在子组件里、触发器用裸
 * `<button>`,triggerRef 恒为 null —— 键盘用户关窗后焦点落到 `<body>`。
 */
export function ConceptMembersTrigger({
  concept,
  children,
  sessionToday,
  expectedDailySession,
}: {
  concept: string;
  children: React.ReactNode;
  /** 新鲜度基准,透传给弹窗;由调用方从 `/session` 取,弹窗不自己造 now。 */
  sessionToday?: string | null;
  expectedDailySession?: string | null;
}) {
  const tr = useUiText();
  const [open, setOpen] = useState(false);
  const [mounted, setMounted] = useState(false);
  const onOpenChange = (next: boolean) => {
    if (next) setMounted(true);
    setOpen(next);
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogTrigger asChild>
        <button
          type="button"
          title={tr("查看 {concept} 的成分股", { concept })}
          className="cursor-pointer text-left hover:underline hover:underline-offset-2"
        >
          {children}
        </button>
      </DialogTrigger>
      {mounted ? (
        <ConceptMembersPanel
          concept={concept}
          open={open}
          sessionToday={sessionToday}
          expectedDailySession={expectedDailySession}
        />
      ) : null}
    </Dialog>
  );
}
