"use client";

/**
 * ③ 回测墙 —— 它真的在跑量化研究。
 *
 * 2026-10-03 二次重构:上一版把"量化能力"压缩成了一张读数的行表,整页因此
 * 读起来像文档。这一版把它展开成会动的真实物件:**每条曲线都是真回测**,
 * 来自 `selenespace.db` backtests 表的 equity_curve_json(降采样 56 点)。
 *
 * 诚实纪律(军规 tile 三选一):曲线**不挑赢家** —— 7 条里 5 条是亏损,
 * ETH 1d 那条 -78%。做量化研究的价值不在"展示漂亮的收益",在"亏损也记录在案"。
 *
 * 动效:卡片入场 stagger(每张错开 60ms),曲线进入视口时画出来。
 */

import { REAL_BACKTESTS, SCALE_FACTS } from "@/lib/landing/real-data";
import { EquityCurve } from "../equity-curve";
import { Reveal, SectionMarker } from "../primitives";

/** 曲线的颜色只编码盈亏:正=pos 绿,负=neg 红(不用多彩装饰) */
function curveColor(ret: number) {
  return ret >= 0 ? "#61bc66" : "#f3615c";
}

export function BacktestWall() {
  const items = REAL_BACKTESTS;
  const wins = items.filter((b) => b.ret >= 0).length;

  return (
    <section id="backtests" className="lp-section lp-hair-b relative">
      <div className="lp-container">
        <Reveal>
          <SectionMarker
            label="回测"
            count={`库里共 ${SCALE_FACTS.backtests} 条 · 下面展示 ${items.length} 条 · ${wins} 盈 ${items.length - wins} 亏`}
          />
          <h2 className="lp-h2 mt-8 max-w-[24ch] text-ink">
            它真的在跑量化研究,
            <br className="hidden sm:block" />
            包括跑输的那些。
          </h2>
          <p className="lp-lead lp-measure mt-5">
            下面是研究库里真实的回测结果。曲线来自 backtests 表的 equity_curve_json,
            指标来自 metrics_json,没有一条是示意数据。
            半数以上是亏损,照样入库。这是研究记录,不是成绩单。
          </p>
        </Reveal>

        <div className="mt-12 grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {items.map((b, i) => (
            <Reveal key={b.id} delay={i * 0.06} y={14}>
              <article className="flex h-full flex-col rounded-lg border border-[var(--lp-hair)] bg-surface-1/60 p-4 transition-colors hover:border-[var(--lp-hair-strong)]">
                <div className="flex items-baseline gap-2">
                  <span className="text-[1rem] font-semibold text-ink">
                    {b.symbol}
                  </span>
                  <span className="num font-mono text-[0.75rem] text-ink-3">
                    {b.interval}
                  </span>
                  <span
                    className={`num ml-auto font-mono text-[1rem] font-semibold ${
                      b.ret >= 0 ? "text-pos" : "text-neg"
                    }`}
                  >
                    {b.ret > 0 ? "+" : ""}
                    {b.ret}%
                  </span>
                </div>

                <EquityCurve
                  data={b.curve}
                  color={curveColor(b.ret)}
                  id={`bt-${b.id}`}
                  height={84}
                  width={300}
                  showScan={false}
                  className="mt-3"
                />

                <dl className="num mt-3 grid grid-cols-4 gap-2 border-t border-[var(--lp-hair)] pt-3 font-mono">
                  {[
                    ["Sharpe", b.sharpe.toFixed(2)],
                    ["回撤", `${b.dd}%`],
                    ["笔数", String(b.trades)],
                    ["胜率", `${b.win}%`],
                  ].map(([k, v]) => (
                    <div key={k}>
                      <dt className="text-[0.75rem] text-ink-3">{k}</dt>
                      <dd className="mt-0.5 text-[0.8125rem] text-ink-2">{v}</dd>
                    </div>
                  ))}
                </dl>

                <div className="num mt-3 font-mono text-[0.75rem] text-ink-3">
                  {b.id} · {b.start} → {b.end}
                </div>
              </article>
            </Reveal>
          ))}
        </div>

        <Reveal delay={0.2}>
          <p className="lp-meta mt-8">
            这些是回测,**不是实盘**。回测通过不等于策略可用。
            Selene 的纪律是把"当时知道什么"和"事后知道了什么"分开记账。
          </p>
        </Reveal>
      </div>
    </section>
  );
}
