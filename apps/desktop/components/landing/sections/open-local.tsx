"use client";

/**
 * ⑥ OPEN & LOCAL —— 本地优先当能力讲,不是 license 脚注。
 * 左主张 + 右真实安装命令(机制式 CTA,学 Cursor 的"机制优先"按钮)。
 * 2026-10-07:补 CLI 一条 —— "可自托管/可脚本化"此前只是主张,CLI 是它的证据
 *   (selenespace CLI = 同引擎库的进程内投影,`packages/cli/selene_space_cli`)。
 */

import { HardDrive, GitBranch, Cpu, Terminal } from "lucide-react";
import { Reveal } from "../primitives";
import { SelfDemo } from "../self-demo";
import { useUiText } from "@/components/i18n/use-ui-text"

const POINTS = [
  { icon: HardDrive, text: "研究库落在本机 SQLite,历史不出机器" },
  { icon: GitBranch, text: "AGPL-3.0 开源,代码可读、可自托管" },
  { icon: Terminal, text: "selenespace CLI:同一套引擎,可脚本化、可接 CI" },
  { icon: Cpu, text: "LLM 与数据源可换,不锁死在某个云" },
];

export function OpenLocal() {
  const tr = useUiText()

  return (
    <section id="open-local" className="lp-section-tight lp-hair-b relative">
      <div className="lp-container grid grid-cols-1 gap-12 lg:grid-cols-2 lg:gap-20">
        <Reveal>
          <h2 className="lp-h2 max-w-[16ch] text-ink">
            {tr("你的研究历史,从不离开你的机器。")}
          </h2>
          <ul className="mt-8 flex flex-col gap-4">
            {POINTS.map(({ icon: Icon, text }) => (
              <li key={text} className="flex items-center gap-3.5">
                <Icon className="size-4 shrink-0 text-ink-3" strokeWidth={1.75} />
                <span className="text-[1rem] text-ink-2">{tr(text)}</span>
              </li>
            ))}
          </ul>
        </Reveal>

        <Reveal delay={0.12}>
          <div className="flex flex-col gap-6">
            <SelfDemo />

            <div className="overflow-hidden rounded-lg border border-[var(--lp-hair-strong)] bg-surface-1">
              <div className="flex items-center border-b border-[var(--lp-hair)] px-4 py-3">
                <span className="num font-mono text-[0.75rem] text-ink-3">
                  terminal
                </span>
              </div>
              <pre
                tabIndex={0}
                className="overflow-x-auto px-5 py-5 font-mono text-[0.875rem] leading-[1.85] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50"
              >
                <code>
                  <span className="text-ink-3">$</span>{" "}
                  <span className="text-ink">git clone</span>{" "}
                  <span className="text-ink-2">github.com/papisoV/selenequant</span>
                  {"\n"}
                  <span className="text-ink-3">$</span>{" "}
                  <span className="text-ink">pip install -r requirements.txt</span>
                  {"\n"}
                  <span className="text-ink-3">$</span>{" "}
                  <span className="text-ink">run.bat</span>
                  <span className="text-ink-3">  {tr("# 起 local-api + 前端")}</span>
                  {"\n"}
                  <span className="text-pos">
                    → local-api :8088 · frontend :3000
                  </span>
                </code>
              </pre>
            </div>
          </div>
        </Reveal>
      </div>
    </section>
  );
}
