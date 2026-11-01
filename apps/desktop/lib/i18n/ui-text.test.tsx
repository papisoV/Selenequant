import { describe, it, expect, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import ts from "typescript";
import { CHAINS } from "@/lib/navigation-chains";
import { ALL_PAGES } from "@/lib/page-manifest";
import {
  INDICATORS,
  INDICATOR_CATEGORY_LABELS,
  buildFormulaString,
} from "@/lib/indicators";
import {
  COUNCIL,
  LOOP_EVIDENCE,
  LOOP_QUESTION,
  MEMORY_ENTRIES,
} from "@/lib/landing/real-data";
import { translateUiText, UI_TEXT_EN_SOURCES } from "./ui-text";
import { I18nProvider } from "@/components/i18n/i18n-provider";
import { useUiText } from "@/components/i18n/use-ui-text";
import { LocaleSwitcherCompact } from "@/components/i18n/locale-switcher";

/**
 * 把源码里**写在引号内**的转义序列还原成运行期真值。
 *
 * 为什么不能只写 `s.replace(/\\(.)/g, "$1")`:那把 `\n` 解成字母 `n`。
 * 于是源码 `tr("基准\n0.0005")` 扫出来是 `基准n0.0005`,而运行期键是
 * **真换行**的 `基准
0.0005` —— 两边对不上,覆盖锁误报「已 tr() 但无译文」。
 * 实测 2026-10-17 tearsheet-panel.tsx 的多行 placeholder 就是这形态。
 *
 * 标准转义(`\n`/`\t`/`\r`/`\uXXXX`/`\xXX`/`\u{…}`)按语义解码;
 * 未知转义(`\"`/`\'`/`\\`/`` \` `` 等)按「反斜杠只是转义标记」处理,取该字符。
 */
const escapeRe = /\\(u\{[0-9a-fA-F]+\}|u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|[\s\S])/g;
function unescapeJs(s: string): string {
  return s.replace(escapeRe, (_, esc: string) => {
    switch (esc[0]) {
      case "n": return "\n";
      case "t": return "\t";
      case "r": return "\r";
      case "b": return "\b";
      case "f": return "\f";
      case "v": return "\v";
      case "0": return "\0";
      case "u": return String.fromCodePoint(parseInt(esc.slice(1).replace(/[{}]/g, ""), 16));
      case "x": return String.fromCharCode(parseInt(esc.slice(1), 16));
      default: return esc;
    }
  });
}

/**
 * 收集源码树里出现过的所有字符串字面量。
 *
 * 判据是「这句源串在磁盘上还写得到吗」,而非「是否被 tr() 包着」——
 * 后者由各域覆盖锁负责。本函数只服务「陈旧条目」检查:源串被改名后
 * 表里的旧条目要报出来。
 *
 * 需处理 `\"` 转义(如 `tr("清空回\"全部\"。")`)—— 用 `\\.` 吃掉转义对,
 * 否则含转义的条目会漏收而被误报为陈旧(实测踩过)。
 */
function collectSourceStrings(): Set<string> {
  const out = new Set<string>();
  const roots = ["components", "lib", "app"];
  const walk = (dir: string) => {
    let entries: string[];
    try {
      entries = readdirSync(dir);
    } catch {
      return;
    }
    for (const name of entries) {
      const p = join(dir, name);
      if (statSync(p).isDirectory()) {
        if (name === "node_modules" || name === ".next") continue;
        walk(p);
        continue;
      }
      if (!/\.(ts|tsx)$/.test(name) || /\.test\.(ts|tsx)$/.test(name)) continue;
      const text = readFileSync(p, "utf8");
      const unescape = unescapeJs;
      for (const m of text.matchAll(/"((?:\\.|[^"\\\n])*)"/g)) {
        if (/[一-鿿]/.test(m[1])) out.add(unescape(m[1]));
      }
      for (const m of text.matchAll(/`((?:\\.|[^`\\\n])*)`/g)) {
        if (/[一-鿿]/.test(m[1])) out.add(unescape(m[1]));
      }
    }
  };
  for (const r of roots) walk(r);
  return out;
}

// 覆盖锁 + 源串翻译契约。改动 page-manifest / navigation-chains 的文案而忘了
// 更新 ui-text.ts 时，第一条会变红(防静默失配留下中文)。

describe("translateUiText", () => {
  it("zh-CN 未命中 → 原样返回源串(源串即中文)", () => {
    expect(translateUiText("zh-CN", "主力监控")).toBe("主力监控");
    expect(translateUiText("zh-CN", "从未登记过的串")).toBe("从未登记过的串");
  });

  it("en 命中 → 返回译文", () => {
    expect(translateUiText("en", "主力监控")).toBe("Main Force Monitor");
    expect(translateUiText("en", "策略工厂")).toBe("Strategy Factory");
  });

  it("en 未命中 → 优雅降级为源串(别处新增的页面不会崩)", () => {
    expect(translateUiText("en", "某个新页面标题")).toBe("某个新页面标题");
  });

  it("支持 {param} 占位替换", () => {
    expect(
      translateUiText("en", "{pages} 页 · {watchlist} 自选", { pages: 12, watchlist: 3 }),
    ).toBe("12 pages · 3 watchlist");
    expect(translateUiText("en", "{exchange} · 永续", { exchange: "binance" })).toBe(
      "binance · Perp",
    );
  });
});

describe("覆盖锁：数据层文案必须全部登记", () => {
  it("每条导航链名都有 en 译文", () => {
    const missing: string[] = [];
    for (const scene of Object.keys(CHAINS) as Array<keyof typeof CHAINS>) {
      for (const chain of CHAINS[scene]) {
        if (translateUiText("en", chain.label) === chain.label) missing.push(chain.label);
      }
    }
    expect(missing, `未登记的链名(会留中文): ${missing.join(" / ")}`).toEqual([]);
  });

  it("每条页面标题都有 en 译文(纯 ASCII 标题按设计豁免)", () => {
    // 已是英文的标题(如 "CVD"/"VPVR"/"Q-Evolve")无需翻译 —— 两个 locale 下相同
    const asciiOnly = (s: string) => !/[一-鿿]/.test(s);
    const missing: string[] = [];
    for (const p of ALL_PAGES) {
      if (asciiOnly(p.title)) continue;
      if (translateUiText("en", p.title) === p.title) missing.push(`${p.slug}: ${p.title}`);
    }
    expect(missing, `未登记的页面标题(会留中文): ${missing.join(" / ")}`).toEqual([]);
  });

  it("表里没有陈旧条目(源串已不再出现于任何源文件)", () => {
    // 为什么扫盘而不维护白名单:B0 只有 chrome 几十条,手写放行清单还行;
    // B1 起每个批次往表里加几百条组件源串,白名单必然腐烂(漏登 = 假绿)。
    // 改为直接扫源码树 —— 「表里这条源串在磁盘上还找得到吗」是真正要守的
    // 不变量:源串被改写(重命名/改标点)而表没同步时,这条就该报出来。
    const sources = collectSourceStrings();
    const dataLayer = new Set<string>();
    for (const scene of Object.keys(CHAINS) as Array<keyof typeof CHAINS>) {
      for (const chain of CHAINS[scene]) dataLayer.add(chain.label);
    }
    for (const p of ALL_PAGES) dataLayer.add(p.title);

    const stale = UI_TEXT_EN_SOURCES.filter(
      (s) => !dataLayer.has(s) && !sources.has(s),
    );
    expect(stale, `表中陈旧条目(源串已不在任何源文件中): ${stale.join(" / ")}`).toEqual([]);
  });
});

describe("覆盖锁：指标目录(B1)", () => {
  it("每个指标分类标签都有 en 译文(纯 ASCII 标签按设计豁免)", () => {
    // 与页面标题同一条豁免:"AI" 两 locale 同形,登记了也是恒等映射 ——
    // 断言它会永远失败,故按「无中文字符即豁免」处理。
    const asciiOnly = (s: string) => !/[一-鿿]/.test(s);
    const missing = (Object.values(INDICATOR_CATEGORY_LABELS) as string[]).filter(
      (l) => !asciiOnly(l) && translateUiText("en", l) === l,
    );
    expect(missing, `未登记的分类标签: ${missing.join(" / ")}`).toEqual([]);
  });

  it("每条指标描述都有 en 译文", () => {
    const asciiOnly = (s: string) => !/[一-鿿]/.test(s);
    const missing: string[] = [];
    for (const m of INDICATORS) {
      if (asciiOnly(m.description)) continue;
      if (translateUiText("en", m.description) === m.description) missing.push(`${m.id}: ${m.description}`);
    }
    expect(missing, `未登记的指标描述: ${missing.join(" / ")}`).toEqual([]);
  });

  it("每个参数标签都有 en 译文(经 buildFormulaString 实证)", () => {
    // 直接断言参数的 label 字段覆盖不全,因为 PARAM_LABELS 是模块私有。
    // 改从公式串走一遍:buildFormulaString 用 tr 拼串,若某标签漏译,
    // 串里会残留中文字符 —— 这正是 en 模式下用户会看到的破绽。
    const missing: string[] = [];
    for (const m of INDICATORS) {
      const formula = buildFormulaString(m, undefined, (s) => translateUiText("en", s));
      if (/[一-鿿]/.test(formula)) missing.push(`${m.id}: ${formula}`);
    }
    expect(missing, `公式串残留中文(参数标签漏译): ${missing.join(" / ")}`).toEqual([]);
  });
});

describe("覆盖锁：research 域(B2)", () => {
  /**
   * 判据是「扫到的 tr() 字面量都有译文」,不是「表里有这几百条」——
   * 后者等于手抄一份清单,加一条忘一条(白名单腐烂,实测踩过)。
   *
   * 扫 `components/research` + `components/agent` 里所有 `tr("…")` 的第一实参:
   * 被 tr() 包住 = 意图就是双语,查不到译文就会在 en 模式留中文,必红。
   * 未包裹的文件不在扫描面内 —— 这正是分批(B2/B3)能安全落地的原因:
   * 本批锁本批,下一批自己扩张扫描面。
   *
   * 纯 ASCII 串按设计豁免(如 "AI"/"OOS" 两 locale 同形)。
   */
  const TR_CALL_RE = /\btr\(\s*(["'])((?:\\.|(?!\1)[^\\\n])*)\1/g;

  const collectWrapped = (): Map<string, string> => {
    const out = new Map<string, string>(); // 源串 -> 首个 file:line
    const roots = ["components/research", "components/agent"];
    const walk = (dir: string) => {
      let entries: string[];
      try {
        entries = readdirSync(dir);
      } catch {
        return;
      }
      for (const name of entries) {
        const p = join(dir, name);
        if (statSync(p).isDirectory()) {
          if (name === "node_modules" || name === ".next") continue;
          walk(p);
          continue;
        }
        if (!/\.(ts|tsx)$/.test(name) || /\.test\.(ts|tsx)$/.test(name)) continue;
        readFileSync(p, "utf8")
          .split(/\r?\n/)
          .forEach((line, i) => {
            for (const m of line.matchAll(TR_CALL_RE)) {
              const src = unescapeJs(m[2]);
              if (!/[一-鿿]/.test(src)) continue;
              if (!out.has(src)) out.set(src, `${p}:${i + 1}`);
            }
          });
      }
    };
    for (const r of roots) walk(r);
    return out;
  };

  it("扫到的 tr() 源串逐条有 en 译文(或已在别域表登记)", () => {
    const wrapped = collectWrapped();
    // 扫描面自证:研究域文件不少,若正则失效扫出 0 条会假绿。
    expect(wrapped.size, "扫描面异常(扫到的 tr() 源串过少,疑正则有误)").toBeGreaterThan(300);

    const missing: string[] = [];
    for (const [src, at] of wrapped) {
      if (translateUiText("en", src) === src) missing.push(`${at} ${src}`);
    }
    expect(missing, `research 域已包裹但无译文(会留中文): ${missing.join(" / ")}`).toEqual([]);
  });
});

describe("覆盖锁：layout 域(B3)", () => {
  /**
   * 与 B2 同构:扫 `components/layout` 里所有 `tr("…")` 的第一实参,
   * 逐条断言有 en 译文。本批锁本批 —— 未包裹的文件不在扫描面内,
   * 而这正是分批能安全推进的原因。
   *
   * scan 面自证阈值取 100:本批实测 159 条;若正则失效扫出 0 条会假绿。
   */
  const TR_CALL_RE = /\btr\(\s*(["'])((?:\\.|(?!\1)[^\\\n])*)\1/g;

  const collectWrapped = (): Map<string, string> => {
    const out = new Map<string, string>();
    const walk = (dir: string) => {
      let entries: string[];
      try {
        entries = readdirSync(dir);
      } catch {
        return;
      }
      for (const name of entries) {
        const p = join(dir, name);
        if (statSync(p).isDirectory()) {
          if (name === "node_modules" || name === ".next") continue;
          walk(p);
          continue;
        }
        if (!/\.(ts|tsx)$/.test(name) || /\.test\.(ts|tsx)$/.test(name)) continue;
        readFileSync(p, "utf8")
          .split(/\r?\n/)
          .forEach((line, i) => {
            for (const m of line.matchAll(TR_CALL_RE)) {
              const src = unescapeJs(m[2]);
              if (!/[一-鿿]/.test(src)) continue;
              if (!out.has(src)) out.set(src, `${p}:${i + 1}`);
            }
          });
      }
    };
    walk("components/layout");
    return out;
  };

  it("扫到的 tr() 源串逐条有 en 译文(或已在别域表登记)", () => {
    const wrapped = collectWrapped();
    expect(wrapped.size, "扫描面异常(扫到的 tr() 源串过少,疑正则有误)").toBeGreaterThan(100);

    const missing: string[] = [];
    for (const [src, at] of wrapped) {
      if (translateUiText("en", src) === src) missing.push(`${at} ${src}`);
    }
    expect(missing, `layout 域已包裹但无译文(会留中文): ${missing.join(" / ")}`).toEqual([]);
  });
});

describe("覆盖锁：全站 tr() 引用完整性(跨域补漏)", () => {
  /**
   * B2/B3 两条锁各自只扫**本批目录**(components/research、components/layout),
   * 这是分批落地的必要设计。代价是一整类盲区:目录之外(components/shared、
   * components/agent、app 下的 page.tsx …)的 tr("…") 从来没人查。
   *
   * 实测:补这条锁时扫出 6 处已 tr() 但无译文的源串,全在 components/shared/
   * —— B2/B3 的扫描面之外,en 模式下会静默留中文(不是没包,是包了没译文)。
   *
   * 判据与前两条同构(扫到的 tr() 字面量逐条断言有 en 译文),但**扫描面是全站**。
   * 这样任何目录里新增 tr() 而忘登记译文,都会在本门变红,而不是等某个
   * 目录轮到自己那批锁。
   */
  const TR_CALL_RE = /\btr\(\s*(["'])((?:\\.|(?!\1)[^\\\n])*)\1/g;

  it("任何目录里 tr(\"中文\") 都有 en 译文(不限于已锁目录)", () => {
    const out = new Map<string, string>(); // 源串 -> 首个 file:line
    const roots = ["components", "lib", "app"];
    const walk = (dir: string) => {
      let entries: string[];
      try {
        entries = readdirSync(dir);
      } catch {
        return;
      }
      for (const name of entries) {
        const p = join(dir, name);
        if (statSync(p).isDirectory()) {
          if (name === "node_modules" || name === ".next") continue;
          walk(p);
          continue;
        }
        if (!/\.(ts|tsx)$/.test(name) || /\.test\.(ts|tsx)$/.test(name)) continue;
        readFileSync(p, "utf8")
          .split(/\r?\n/)
          .forEach((line, i) => {
            for (const m of line.matchAll(TR_CALL_RE)) {
              const src = unescapeJs(m[2]);
              if (!/[一-鿿]/.test(src)) continue;
              if (!out.has(src)) out.set(src, `${p}:${i + 1}`);
            }
          });
      }
    };
    for (const r of roots) walk(r);
    // 扫描面自证:全站 tr() 源串数量级 (research+layout 已 > 600)。
    expect(out.size, "全站扫描面异常(扫到的 tr() 源串过少,疑正则有误)").toBeGreaterThan(400);

    const missing: string[] = [];
    for (const [src, at] of out) {
      if (translateUiText("en", src) === src) missing.push(`${at} ${src}`);
    }
    expect(missing, `已 tr() 但无译文(会留中文): ${missing.join(" / ")}`).toEqual([]);
  });
});

describe("覆盖锁：模板串(B3-b 补)", () => {
  /**
   * 为什么要有这条:前两条覆盖锁只认 `tr("…")` 的**字符串字面量**第一实参。
   * `` `最近 ${n} 天的变化` `` 这种模板串既不被 codemod 包裹(它只处理
   * isStringLiteral),也不被 REGEX 扫到 —— 于是「裸模板串」成一整类盲区。
   * 实测 B3-b:54 处 CJK 模板串,其中 43 处在渲染位,全部漏过两道门。
   *
   * 判据用 AST 而非正则,因为两条都只有 AST 分得清:
   *   - `\`${tr(a)} ${tr(b)}\`` 的中文在**内层 tr 实参**里,模板本身的字面段
   *     无中文 —— 正则会把整行看成一坨而误报。
   *   - `` \`cls ${x === "强" ? …}\` `` 的中文是**比较操作数**(后端协议值),
   *     本就不该翻译 —— 正则同样分不出它与展示串。
   * 模板串的字面段(head + 各 span 的 literal)才是「会被原样渲染的文案」。
   *
   * 两处按设计豁免(非渲染位,且**不该**翻译):
   *   - pa-panel.tsx  `[最近诊断上下文] …`  拼给 LLM 的 prompt 前缀
   *   - promote-dialog.tsx `被 revision v…`  落库的 status_note 值
   * 这类「协议值/prompt」中文是**数据**不是**文案**,翻译会改变语义。
   * 豁免按**内容指纹**而非 file:line —— 行号会被 codemod 整体位移(实测踩过)。
   */
  const EXEMPT_RAW = [
    "[最近诊断上下文]", // pa-panel:LLM prompt 前缀
    "被 revision v", // promote-dialog:落库状态说明
  ];
  const isExempt = (raw: string) => EXEMPT_RAW.some((e) => raw.includes(e));

  let SCANNED_FILES = 0;

  const collectRenderTemplates = (): Map<string, string> => {
    const out = new Map<string, string>();
    const walk = (dir: string) => {
      let entries: string[];
      try {
        entries = readdirSync(dir);
      } catch {
        return;
      }
      for (const name of entries) {
        const p = join(dir, name);
        if (statSync(p).isDirectory()) {
          if (name === "node_modules" || name === ".next") continue;
          walk(p);
          continue;
        }
        if (!/\.(ts|tsx)$/.test(name) || /\.test\.(ts|tsx)$/.test(name)) continue;
        SCANNED_FILES++;

        const sf = ts.createSourceFile(
          p,
          readFileSync(p, "utf8"),
          ts.ScriptTarget.Latest,
          true,
          ts.ScriptKind.TSX,
        );
        const insideTr = (node: ts.Node): boolean => {
          let q = node.parent as ts.Node | undefined;
          while (q) {
            if (
              ts.isCallExpression(q) &&
              ts.isIdentifier(q.expression) &&
              (q.expression.text === "tr" || q.expression.text === "tl")
            ) {
              return true;
            }
            q = q.parent as ts.Node | undefined;
          }
          return false;
        };
        const visit = (n: ts.Node) => {
          if (ts.isTemplateExpression(n) || ts.isNoSubstitutionTemplateLiteral(n)) {
            // 只取字面段:插值表达式里若已 tr() 过,其中文不属本模板串
            const raw = ts.isTemplateExpression(n)
              ? n.head.text + n.templateSpans.map((s) => s.literal.text).join("")
              : n.text;
            if (/[一-鿿]/.test(raw) && !insideTr(n) && !isExempt(raw)) {
              const par = n.parent as ts.Node;
              // 渲染位:JSX 表达式 / JSX 属性 / 函数实参 / return
              const rendering =
                ts.isJsxExpression(par) ||
                ts.isJsxAttribute(par) ||
                ts.isCallExpression(par) ||
                ts.isReturnStatement(par);
              if (rendering) {
                const { line } = sf.getLineAndCharacterOfPosition(n.getStart(sf));
                const key = raw.slice(0, 60);
                if (!out.has(key)) out.set(key, `${p}:${line + 1}`);
              }
            }
          }
          ts.forEachChild(n, visit);
        };
        visit(sf);
      }
    };
    for (const r of ["components/research", "components/agent"]) walk(r);
    return out;
  };

  it("渲染位没有未包 tr 的中文模板串(防整类盲区复发)", () => {
    const all = collectRenderTemplates();
    // 扫描面自证:真跑过 AST 遍历才可能为 0;若 typescript 未解析出任何源码
    // (import 失败/遍历失效),这里会以「未捕捉到面」的形态暴露,而非静默假绿。
    expect(SCANNED_FILES, "AST 扫描面为 0,疑遍历失效").toBeGreaterThan(100);

    const bad = [...all.entries()].map(([k, at]) => `${at} ${JSON.stringify(k)}`);
    expect(
      bad,
      `渲染位中文模板串未包 tr(en 模式会留中文): ${bad.join(" / ")}`,
    ).toEqual([]);
  });
});

describe("覆盖锁：数据常量渲染位(B3-b 补)", () => {
  /**
   * 为什么要有这条:前面三条覆盖锁只认 `tr("…")` 的**字符串字面量**第一实参
   * (正则)和渲染位的**模板串**(AST)。两者都看不见第三类:
   *
   *   `{CONST.map((m) => <span>{m.label}</span>)}`
   *   常量在模块作用域定义,渲染点是**属性访问**(`m.label`),`tr` 的第一实参
   *   不是字面量 —— 正则扫不到;也不是模板串 —— 模板锁扫不到。
   *
   * 实测 B3-b:这道门加进来之前,研究域里 34 处 `tr(CONST.prop)` 包裹点
   * 对既有两道锁完全不可见(改完全绿),但 en 模式下这些串全部留中文。
   * 判据用 AST 走「模块级含中文的数据常量 → 别名 → 渲染位属性访问/数组元素」:
   * 只有这一条链能同时看到常量定义与渲染点两端。
   *
   * 扫的是**未包 tr 的渲染位**:包着的不报(它们本来就要有译文,归前三条锁),
   * 没包的必报 —— 这正是「原样渲染的中文」的定义。
   *
   * 自证:`SITES` 是扫到的**全部**常量消费点(含已包裹),实测 41;
   * 若常量解析或渲染位遍历失效会趋 0,断言 > 20 挡住假绿。
   */
  const CJK = /[一-鿿]/;
  const TEXT_ATTRS = new Set(["title", "aria-label", "placeholder", "alt", "label"]);
  const MAP_METHODS = new Set(["map", "filter", "find", "forEach", "some", "every", "flatMap"]);

  it("渲染位没有未包 tr 的中文数据常量(属性访问 / 数组元素两种形状)", () => {
    const bad: string[] = [];
    let SITES = 0;
    let FILES = 0;

    const hasCjk = (x: ts.Node): boolean => {
      let has = false;
      const w = (n: ts.Node) => {
        if (
          (ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n)) &&
          CJK.test(n.text)
        )
          has = true;
        if (ts.isTemplateExpression(n)) {
          if (CJK.test(n.head.text)) has = true;
          for (const s of n.templateSpans) if (CJK.test(s.literal.text)) has = true;
        }
        ts.forEachChild(n, w);
      };
      w(x);
      return has;
    };
    const insideTr = (node: ts.Node): boolean => {
      let q = node.parent as ts.Node | undefined;
      while (q) {
        if (
          ts.isCallExpression(q) &&
          ts.isIdentifier(q.expression) &&
          (q.expression.text === "tr" || q.expression.text === "tl")
        )
          return true;
        q = q.parent as ts.Node | undefined;
      }
      return false;
    };

    const walk = (dir: string) => {
      let entries: string[];
      try {
        entries = readdirSync(dir);
      } catch {
        return;
      }
      for (const name of entries) {
        const p = join(dir, name);
        if (statSync(p).isDirectory()) {
          if (name === "node_modules" || name === ".next") continue;
          walk(p);
          continue;
        }
        if (!/\.(ts|tsx)$/.test(name) || /\.test\.(ts|tsx)$/.test(name)) continue;
        FILES++;
        const sf = ts.createSourceFile(
          p,
          readFileSync(p, "utf8"),
          ts.ScriptTarget.Latest,
          true,
          ts.ScriptKind.TSX,
        );

        // 1) 模块级「含中文的数据常量」
        //    cjkConst  : 常量名 -> 含中文的属性名集合(属性访问形状 m.label)
        //    cjkValConst: 常量名集合,其**值本身**含中文(数组/字典整体),用于
        //                 `CONST[k]` 元素访问形状 —— 这类常量的值不挂在某个
        //                 具名属性上,键是运行期变量,故按整常量收录。
        //                 (B3-b 补的补:行为扫描扫不到元素访问,实测 7 处数据门
        //                 控面板静默留中文,见 46 处原型清单。)
        const cjkConst = new Map<string, Set<string>>();
        const cjkValConst = new Set<string>();
        for (const st of sf.statements) {
          if (!ts.isVariableStatement(st)) continue;
          for (const d of st.declarationList.declarations) {
            if (!ts.isIdentifier(d.name) || !d.initializer) continue;
            const init = d.initializer;
            if (ts.isArrowFunction(init) || ts.isFunctionExpression(init)) continue;
            if (hasCjk(init)) cjkValConst.add(d.name.text);
            const props = new Set<string>();
            const wp = (x: ts.Node) => {
              if (ts.isPropertyAssignment(x) && ts.isIdentifier(x.name) && hasCjk(x.initializer))
                props.add(x.name.text);
              ts.forEachChild(x, wp);
            };
            wp(init);
            if (props.size) cjkConst.set(d.name.text, props);
          }
        }
        if (!cjkConst.size && !cjkValConst.size) continue;

        // 2) 别名:.map(param => …) 的 param / const x = CONST / CONST[k] / CONST.find(…)
        const aliases = new Map<string, string>();
        const v2 = (n: ts.Node) => {
          if (
            ts.isCallExpression(n) &&
            ts.isPropertyAccessExpression(n.expression) &&
            MAP_METHODS.has(n.expression.name.text) &&
            n.arguments.length >= 1
          ) {
            const recv = n.expression.expression;
            const cname = ts.isIdentifier(recv)
              ? recv.text
              : ts.isPropertyAccessExpression(recv) && ts.isIdentifier(recv.expression)
                ? recv.expression.text
                : null;
            const fn = n.arguments[0];
            if (cname && cjkConst.has(cname) && (ts.isArrowFunction(fn) || ts.isFunctionExpression(fn)))
              for (const prm of fn.parameters)
                if (ts.isIdentifier(prm.name)) aliases.set(prm.name.text, cname);
          }
          if (ts.isVariableDeclaration(n) && ts.isIdentifier(n.name) && n.initializer) {
            const i = n.initializer;
            if (ts.isIdentifier(i) && cjkConst.has(i.text)) aliases.set(n.name.text, i.text);
            else if (ts.isElementAccessExpression(i) && ts.isIdentifier(i.expression) && cjkConst.has(i.expression.text))
              aliases.set(n.name.text, i.expression.text);
            else if (
              ts.isCallExpression(i) &&
              ts.isPropertyAccessExpression(i.expression) &&
              ts.isIdentifier(i.expression.expression) &&
              cjkConst.has(i.expression.expression.text)
            )
              aliases.set(n.name.text, i.expression.expression.text);
          }
          ts.forEachChild(n, v2);
        };
        v2(sf);

        // 3) 数组元素别名:.map(cb) 且接收者解析到含中文常量 / 该常量的属性
        const elemParams = new Map<string, string>();
        const v3 = (n: ts.Node) => {
          if (
            ts.isCallExpression(n) &&
            ts.isPropertyAccessExpression(n.expression) &&
            MAP_METHODS.has(n.expression.name.text) &&
            n.arguments.length >= 1
          ) {
            const recv = n.expression.expression;
            let label: string | null = null;
            if (ts.isIdentifier(recv)) {
              const c = cjkConst.has(recv.text) ? recv.text : aliases.get(recv.text);
              if (c) label = recv.text;
            } else if (ts.isPropertyAccessExpression(recv) && ts.isIdentifier(recv.expression)) {
              const rootId = recv.expression.text;
              const c = cjkConst.has(rootId) ? rootId : aliases.get(rootId);
              if (c && cjkConst.get(c)!.has(recv.name.text)) label = recv.getText(sf);
            }
            const fn = n.arguments[0];
            if (label && fn && (ts.isArrowFunction(fn) || ts.isFunctionExpression(fn)))
              for (const prm of fn.parameters)
                if (ts.isIdentifier(prm.name)) elemParams.set(prm.name.text, label);
          }
          ts.forEachChild(n, v3);
        };
        v3(sf);

        const at = (n: ts.Node) => {
          const { line } = sf.getLineAndCharacterOfPosition(n.getStart(sf));
          return `${p}:${line + 1}`;
        };
        // 同一个属性渲染位会被 v4 的两条路径各访问一次(JsxExpression 子节点 +
        // JsxAttribute 本身)——按「位置+标签」去重,否则 SITES 翻倍、bad 里同一条
        // 报两遍(实测 tearsheet-panel.tsx:334 曾重复出现)。
        const seen = new Set<string>();
        const report = (node: ts.Node, label: string) => {
          const key = `${at(node)} ${label}`;
          if (seen.has(key)) return;
          seen.add(key);
          SITES++;
          if (!insideTr(node)) bad.push(key);
        };
        const checkProp = (expr: ts.Node) => {
          if (!ts.isPropertyAccessExpression(expr) || !ts.isIdentifier(expr.expression)) return;
          const base = expr.expression.text;
          const prop = expr.name.text;
          const src = cjkConst.has(base) ? base : aliases.get(base);
          if (!src || !cjkConst.get(src)!.has(prop)) return;
          report(expr, `${base}.${prop}`);
        };
        // 元素访问形状 `CONST[key]`:键是运行期变量,无法按属性名匹配,故只要
        // 接收者是「值含中文的模块常量」就报(B3-b 补的补)。`?? fallback` 里的
        // 兜底值往往是键本身(英文 id),不影响判断 —— 渲染出来的仍是中文值。
        const checkElem = (expr: ts.Node) => {
          if (!ts.isElementAccessExpression(expr)) return;
          const recv = expr.expression;
          const base = ts.isIdentifier(recv)
            ? recv.text
            : ts.isPropertyAccessExpression(recv) && ts.isIdentifier(recv.expression)
              ? recv.expression.text
              : null;
          if (!base) return;
          if (!cjkValConst.has(base) && !cjkConst.has(base)) return;
          report(expr, `${base}[…]`);
        };
        // 只在「渲染表达式语法」内下钻,不跨函数/声明边界:
        // 条件分支、字符串拼接、括号、tr()/tl() 实参、模板插值。
        // 其它 CallExpression 不下钻 —— 否则会钻进回调体/声明里误报
        // (实测:alpha-mining-panel:1158 的 `const review = …` 声明被当成渲染位)。
        const checkExpr = (e: ts.Node | undefined) => {
          if (!e) return;
          if (ts.isPropertyAccessExpression(e)) {
            checkProp(e);
            return;
          }
          if (ts.isElementAccessExpression(e)) {
            checkElem(e);
            return;
          }
          if (ts.isConditionalExpression(e)) {
            checkExpr(e.whenTrue);
            checkExpr(e.whenFalse);
            return;
          }
          if (
            ts.isBinaryExpression(e) &&
            (e.operatorToken.kind === ts.SyntaxKind.PlusToken ||
              e.operatorToken.kind === ts.SyntaxKind.QuestionQuestionToken)
          ) {
            checkExpr(e.left);
            checkExpr(e.right);
            return;
          }
          if (ts.isParenthesizedExpression(e)) {
            checkExpr(e.expression);
            return;
          }
          if (ts.isCallExpression(e)) {
            const c = e.expression;
            if (ts.isIdentifier(c) && (c.text === "tr" || c.text === "tl"))
              for (const a of e.arguments) checkExpr(a);
            return;
          }
          if (ts.isTemplateExpression(e)) {
            for (const s of e.templateSpans) checkExpr(s.expression);
            return;
          }
        };
        const v4 = (n: ts.Node) => {
          if (ts.isJsxExpression(n) && n.expression) {
            const parent = n.parent as ts.Node;
            const isRenderAttr =
              ts.isJsxAttribute(parent) &&
              ts.isIdentifier(parent.name) &&
              TEXT_ATTRS.has(parent.name.text);
            const isChild = !ts.isJsxAttribute(parent);
            // key= 不是渲染位(改 locale 应更新文案,而非重挂子树)—— 不报
            if (isChild || isRenderAttr) {
              const e = n.expression;
              if (ts.isIdentifier(e) && elemParams.has(e.text)) report(e, `{${e.text}} <- ${elemParams.get(e.text)}`);
              else if (ts.isIdentifier(e) && cjkConst.has(e.text)) report(e, `{${e.text}}`);
              else checkExpr(e);
            }
          }
          if (
            ts.isJsxAttribute(n) &&
            ts.isIdentifier(n.name) &&
            TEXT_ATTRS.has(n.name.text) &&
            n.initializer &&
            ts.isJsxExpression(n.initializer) &&
            n.initializer.expression
          )
            checkExpr(n.initializer.expression);
          ts.forEachChild(n, v4);
        };
        v4(sf);
      }
    };
    for (const r of ["components/research", "components/agent", "components/layout"]) walk(r);

    expect(FILES, "AST 扫描面为 0,疑遍历失效").toBeGreaterThan(100);
    // 扫描面自证:实测 41 个常量消费点。解析链任一环失效都会趋 0 假绿。
    expect(SITES, "常量消费点过少(疑常量解析或渲染位遍历失效)").toBeGreaterThan(20);
    expect(
      bad,
      `渲染位中文数据常量未包 tr(en 模式会留中文): ${bad.join(" / ")}`,
    ).toEqual([]);
  });
});

describe("覆盖锁：landing 真实研究内容(2026-10-19 补)", () => {
  /**
   * 为什么要有这条:landing 是一整个**静态数据驱动的页面** —— `lib/landing/real-data.ts`
   * 里逐字固化了真实研究库内容(问题标题 / 修订正文 / 实验 keyNumbers / 调查会发言),
   * 组件在渲染位直接 `{q.title}` / `{rev.content}` / `{p.quote}` 输出。
   *
   * 实测(2026-10-19 真渲染扫描 en 模式):`/` 上 25 条中文残留,全部来自这类常量。
   * 上面「数据常量渲染位」锁为什么没抓到?它的 AST 解析链只认**组件文件内**定义的
   * 模块级常量;这里数据在 `lib/landing/real-data.ts`,组件里是 import 进来的,
   * `cjkConst` 看不到定义 → 整类静默漏过。
   *
   * 判据与那条锁同构,但换一条链:直接拿 fixture 里的**真值**当源串,断言每个
   * 会被渲染的字段都查得到 en 译文。这样 fixture 换一批证据、而词表没跟上的时候,
   * 本门变红,而不是等出货后在英文界面看到中文。
   *
   * 逻辑值(isHole 式)豁免:LOOP_EVIDENCE[].verdict 是展示串(渲染点已 tr 包裹),
   * 但同一字段在组件里还参与 `=== "翻案"` 与 `VERDICT_STYLE[...]` 查表 —— 这里只
   * 断言它有译文,不改变取值本身;`mode` 是纯 ASCII enum,不需译文。
   */
  const asciiOnly = (s: string) => !/[一-鿿]/.test(s);
  const missing = (label: string, values: string[]) => {
    const out = values.filter((v) => !asciiOnly(v) && translateUiText("en", v) === v);
    expect(out, `${label} en 模式下会留中文: ${out.join(" / ")}`).toEqual([]);
  };

  it("问题标题与每版正文都有 en 译文", () => {
    // HERO_QUESTION 目前无渲染点(hero 区用自带的 LOOP_NODES 常量),故不纳入 ——
    // 它是死数据,断言它只会让「这条锁到底守什么」变得含糊。
    missing(`question ${LOOP_QUESTION.id} title`, [LOOP_QUESTION.title]);
    for (const rev of LOOP_QUESTION.revisions) missing(`${rev.id} content`, [rev.content]);
    missing(`question ${COUNCIL.questionId} title`, [COUNCIL.questionTitle]);
    missing(`council revision`, [COUNCIL.revision]);
  });

  it("实验标题 / 判决 / 关键数 / 记忆正文 / 调查会发言与裁决都有 en 译文", () => {
    for (const ev of LOOP_EVIDENCE) {
      missing(`${ev.id} episodeTitle`, [ev.episodeTitle]);
      missing(`${ev.id} verdict`, [ev.verdict]);
      missing(`${ev.id} keyNumbers`, [ev.keyNumbers]);
      // mode 是 RECORDED/RECONSTRUCTED/AFTER_THE_FACT 纯 ASCII enum,断言按设计豁免
    }
    for (const m of MEMORY_ENTRIES) {
      missing(`${m.revisionId} kind`, [m.kind]);
      missing(`${m.revisionId} text`, [m.text]);
    }
    for (const p of COUNCIL.perspectives) {
      missing(`${p.key} label`, [p.label]);
      missing(`${p.key} discipline`, [p.discipline]);
      missing(`${p.key} quote`, [p.quote]);
    }
    for (const r of COUNCIL.rulings) {
      missing(`ruling dispute`, [r.dispute]);
      missing(`ruling verdict`, [r.verdict]);
    }
  });

  it("扫描面自证:上述字段里含中文的条数足够多(防 fixture 或本锁失效假绿)", () => {
    // 实测 2026-10-19:33 条含中文的被渲染字段。若 real-data 被清空、或本锁的
    // 取值链断掉,这个数会趋 0 —— 届时上面的断言会「空跑通过」。
    const all = [
      LOOP_QUESTION.title,
      ...LOOP_QUESTION.revisions.map((r) => r.content),
      ...LOOP_EVIDENCE.flatMap((e) => [e.episodeTitle, e.verdict, e.keyNumbers]),
      ...MEMORY_ENTRIES.flatMap((m) => [m.kind, m.text]),
      COUNCIL.questionTitle,
      COUNCIL.revision,
      ...COUNCIL.perspectives.flatMap((p) => [p.label, p.discipline, p.quote]),
      ...COUNCIL.rulings.flatMap((r) => [r.dispute, r.verdict]),
    ].filter((s) => !asciiOnly(s));
    expect(all.length, "含中文的渲染字段过少,疑取值链失效").toBeGreaterThan(25);
  });
});

describe("逻辑值护栏(A 类:中文当协议值,不可包 tr)", () => {
  /**
   * 为什么要有这条**语法**检查,而不是只靠 `scripts/.i18n-risky.json` 的豁免行:
   * 豁免清单按 **file:line** 记录,而 codemod 一跑行号就整体位移 —— 实测
   * B2 批里 `serenity-panel.tsx:360 c.evidence_label === "强"` 与
   * `alpha-mining-panel.tsx:774 syncMsg.startsWith("已写入")` 两处,首轮包裹后
   * 行号漂到 :363/:777,豁免失配 → 被包成 `=== tr("强")`,比较恒 false
   * (英文用户看到全部证据档位掉进 else 分支,静默错色)。
   * 语法判据不受行号影响:`tr(...)` 一旦出现在比较/串方法的操作数位,必是错。
   */
  const LOGIC_WRAP_RE =
    /(?:===|!==|==|!=)\s*tr\(|tr\([^)]*\)\s*(?:===|!==|==|!=)|\.(?:includes|startsWith|endsWith|indexOf|charAt|slice|match|test)\s*\(\s*tr\(/;

  it("源码里没有 tr() 包在比较 / 串方法操作数上", () => {
    const hits: string[] = [];
    const walk = (dir: string) => {
      let entries: string[];
      try {
        entries = readdirSync(dir);
      } catch {
        return;
      }
      for (const name of entries) {
        const p = join(dir, name);
        if (statSync(p).isDirectory()) {
          if (name === "node_modules" || name === ".next") continue;
          walk(p);
          continue;
        }
        if (!/\.(ts|tsx)$/.test(name) || /\.test\.(ts|tsx)$/.test(name)) continue;
        // 逐行查:命中即报 file:line,便于直接跳过去改。
        readFileSync(p, "utf8")
          .split(/\r?\n/)
          .forEach((line, i) => {
            if (LOGIC_WRAP_RE.test(line)) hits.push(`${p}:${i + 1}`);
          });
      }
    };
    for (const r of ["components", "lib", "app"]) walk(r);
    expect(
      hits,
      `tr() 被包在逻辑位上(比较恒 false / 串方法恒 miss): ${hits.join(" / ")}`,
    ).toEqual([]);
  });
});

describe("useUiText", () => {
  afterEach(() => {
    localStorage.clear();
    delete document.documentElement.dataset.locale;
    document.documentElement.lang = "";
  });

  it("provider 缺席(测试裸渲染) → 原样返回源串", () => {
    function Probe() {
      const tr = useUiText();
      return <span data-testid="p">{tr("策略工厂")}</span>;
    }
    render(<Probe />);
    expect(screen.getByTestId("p").textContent).toBe("策略工厂");
  });

  it("provider 内默认 zh-CN → 源串即中文,原样返回", () => {
    function Probe() {
      const tr = useUiText();
      return <span data-testid="p">{tr("策略工厂")}</span>;
    }
    render(
      <I18nProvider>
        <Probe />
      </I18nProvider>,
    );
    expect(screen.getByTestId("p").textContent).toBe("策略工厂");
  });

  it("provider 内已存 en → 译成英文", () => {
    localStorage.setItem("selenespace-locale", "en");
    function Probe() {
      const tr = useUiText();
      return <span data-testid="p">{tr("策略工厂")}</span>;
    }
    render(
      <I18nProvider>
        <Probe />
      </I18nProvider>,
    );
    expect(screen.getByTestId("p").textContent).toBe("Strategy Factory");
  });
});

describe("LocaleSwitcherCompact 端到端切换", () => {
  afterEach(() => {
    localStorage.clear();
    delete document.documentElement.dataset.locale;
    document.documentElement.lang = "";
  });

  function Probe() {
    const tr = useUiText();
    return <span data-testid="label">{tr("策略工厂")}</span>;
  }

  it("默认中文 → 点切换器变英文,再点回中文(文案与 data-locale 同步翻转)", () => {
    render(
      <I18nProvider>
        <LocaleSwitcherCompact />
        <Probe />
      </I18nProvider>,
    );
    // 默认 zh-CN:中文文案 + 按钮提示"Switch to English"
    expect(screen.getByTestId("label").textContent).toBe("策略工厂");
    expect(document.documentElement.dataset.locale).toBe("zh-CN");

    fireEvent.click(screen.getByTitle("Switch to English"));
    // 切到 en:英文文案 + data-locale 翻转 + 按钮提示变中文
    expect(screen.getByTestId("label").textContent).toBe("Strategy Factory");
    expect(document.documentElement.dataset.locale).toBe("en");
    expect(screen.getByTitle("切换到中文")).toBeTruthy();

    fireEvent.click(screen.getByTitle("切换到中文"));
    expect(screen.getByTestId("label").textContent).toBe("策略工厂");
    expect(document.documentElement.dataset.locale).toBe("zh-CN");
  });
});
