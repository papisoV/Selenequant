# 绘图工具对标 TradingView —— 调研与改造清单

> 2026-10-21 | 分支 `feat/chart-drawing` | 本文只做调研，不含任何生产代码改动
>
> 结论先行：**我们现在只有 1 个绘图工具（斐波那契网格），TV 有 60+ 个；且这唯一的工具
> 把档位线画成了通栏直线，而 TV 的默认几何是"两个锚点之间的线段"。** 修复不是改两个数字，
> 而是把"画什么"收敛成唯一一份几何来源，再按 TV 的工具注册表模式扩展。

---

## 证据分级

全文每个关键断言标注来源等级：

| 标记 | 含义 | 本文中的对应证据 |
|---|---|---|
| ⬤ 实测 | 本机克隆源码逐行读过 / 浏览器渲染实测 | 参考项目源码、我们自己的 `file:line` |
| ◐ 报道 | 官方文档原文，但我没跑起来验证行为 | TV 帮助中心文档 |
| ○ 声明 | 项目自述/README/包元数据 | stars/license/version |

TV 官方文档全部来自 `https://www.tradingview.com/support/solutions/<id>-<slug>/`，每条附 URL。
无法一手核实的（如"某工具内部如何处理对数刻度"）一律标「报道级，未核实」。

---

## 一、TradingView 绘图工具全表

来源：`43000703396-drawing-tools-available-on-tradingview`（总目录）＋ 21 篇单工具文档。⬤

### 1.1 工具家族与点数

TV 的目录分 11 个家族。下表的「点数」= 落笔需要点击几次；未在单篇文档中明写的，标 `?`。

| 家族 | 工具 | 点数 | 关键样式能力（单篇文档明写） |
|---|---|---|---|
| **光标** | Cross / Dot / Arrow / Demonstration / Magic / Eraser | — | 非绘图工具 |
| **趋势** | Trendline、Ray、Info line、Extended line、Trend angle | 2 | — |
| | Horizontal line、Horizontal ray、Vertical line、Crossline | 1 | Vertical line 有 **Extend**（穿越指标面板）、Alert on cross |
| | Parallel / Regression / Flat top-bottom / Disjoint channel | 3 | — |
| | Classic / Inside / Schiff / Modified Schiff pitchfork | 3 | — |
| **斐波那契** | 见 §2（11 个） | 2 或 3 | 见 §2 |
| **江恩** | Gann box、Gann square fixed、Gann square、Gann fan | 2 | — |
| **形态** | XABCD(5)、Cypher(5)、Head&shoulders(7)、ABCD(4)、Triangle(4)、Three drives(7)、Elliott waves(4–6) | 4–7 | — |
| **周期** | Cyclic lines(2)、Time cycles(2)、Sine line(2) | 2 | Time cycles 有隔线背景填充 |
| **预测/测量** | Long/Short position(1)、Position forecast(2)、Bars pattern(2)、Ghost feed(2)、Sector(3) | 1–3 | — |
| | Price range、Date range、Date and price range | 2 | 三者都有 **Extend**（仅 Date/Price range）；Info 面板有 **Stats** 下拉 |
| **成交量** | Anchored VWAP(1)、Fixed range VP(2)、Anchored VP(1) | 1–2 | — |
| **几何形状** | Rectangle(2)、Rotated rectangle(3)、Path、Circle(2)、Ellipse(3)、Polyline、Triangle(3)、Arc(3)、Curve(2)、Double curve(2)、Brush/Highlighter | 2–3 | — |
| **标注** | Text(1)、Note(2)、Price note(2)、Pin(1)、Table(1)、Callout(2)、Comment(1)、Price label(1)、Signpost(1)、Image(1)、X posts、Icons | 1–2 | — |

**总目录原文列举的斐波那契 11 项**（◐，逐字）：
> "Fibonacci retracement / Trend-based fib extension / Fib channel / Fib time zone /
> Fib speed resistance fan / Trend-based fib time / Fib circles / Fib spiral /
> Fib speed resistance arcs / Fib wedge / Pitchfan"

### 1.2 三个跨工具的系统级能力（这些不是单工具样式，是整层机制）

| 能力 | 文档 | 要点（◐ 原文） |
|---|---|---|
| **Object Tree 嵌套分组** | `43000795451` | "There's no limit on nesting depth." / "Selecting a group selects all of its contents, including nested groups and drawings at every level." / "Deleting a group deletes it with all its nested groups and drawings." / 空组自动移除 / "The top-level group sets the sync mode for everything inside it." |
| **逐周期可见性** | `43000484410` | "Open the drawing object settings, select Visibility tab and enable intervals where the drawing will be displayed." |
| **多选批量位移** | `43000682552` | Ctrl/Cmd 多选、方向键微调、拖拽，以及 **Displacement** 页签：价格支持 `+ − × ÷`，bar index 只支持 `+ −` |

第四项值得一提：**逐档位的颜色/不透明度/自定义比例**，在 fib 家族的 6 篇文档里都以
"Additional Levels" 段落出现（见 §2.2），不是个别工具的恩赐。

---

## 二、斐波那契家族契约

### 2.1 11 个工具的几何定义

| 工具 | 点数 | 几何 | 文档 |
|---|---|---|---|
| **fib retracement** | 2 | 两锚点纵向距离按比例切水平线。0 / 1 由两锚点定义。1–24 条 | `43000518158` |
| **trend-based fib extension** | 3 | 第 3 点（回撤终点）起，把 p0→p1 的位移按比例投影 | `43000518137` |
| **fib channel** | 3 | 前两点定趋势线，第三点定通道宽度（即 0 与 1 两个电平） | `43000791610` |
| **fib time zone** | 2 | 两锚点定基准间隔，**竖向**线落在斐波那契数列倍数上 | `43000518155` |
| **trend-based fib time** | 3 | 先画趋势线，竖向线按斐波那契比例铺在时间轴上 | `43000518136` |
| **fib speed resistance fan** | 2 | 从起点发散的射线扇；有 Price/Time Levels 两组 | `43000518156` |
| **fib circles** | 2 | 11 个同心/嵌套圆 | `43000518159` |
| **fib spiral** | 2 | 半径按黄金比递增；有 **Counterclockwise** 勾选 | `43000791611` |
| **fib speed resistance arcs** | 2 | 最多 11 条弧；有 **Full Circles** 开关 | `43000518157` |
| **fib wedge** | 2 | 从趋势起点发散的一组弧（文档原文："drawn according to the trend movement (two points)"） | `43000518153` |
| **pitchfan** | 3 | 三点定中位线 + 上下各一组平行线 | `43000518143` |

> **点数以源码为准的交叉验证**（⬤）：`lwcd/src/core/specs.ts:353` 起的 `OVERLAY_SPECS`
> 里，`fib-wedge` 与 `pitchfan` 都标 `pointCount: 3`，与 TV 文档「wedge 是两点」不一致。
> 这一点我不下结论 —— **标「报道级冲突，未核实」**。落地时按 3 点实现更安全（第 3 点退化时
> 等价于 2 点），两种读法都兼容。

### 2.2 样式面（13 个旋钮，逐字引用 `43000518158`）

> "Trend Line — 勾选切换趋势线可见性；旁侧切换设颜色、透明度、粗细、线型。"
> "Levels Line — 设所有档位线的粗细与线型。"
> "**Extend Lines Left/Right — Extends levels' lines indefinitely to the left or right.**"
> "**Additional Levels — 左侧勾选切换每条附加档位的可见性。也可以为每条档位输入自定义比例，
> 并单独设颜色与不透明度。**"
> "Use one color — 一个下拉，让所有线与背景同色。"
> "Background — 切换档位之间背景填充的可见性与不透明度。"
> "Reverse — 垂直翻转回撤方向。"
> "Prices — 切换档位旁价格数字的可见性。"
> "**Levels — 切换档位数值文字的可见性。可以选绝对值或百分比显示在档位旁。**"
> "Labels — 设档位数值沿线的位置。"
> "Text — 切换档位旁文字；位置可调。"
> "Font size — 改变标签与文字字号。"
> "**Fib levels based on log scale — 对数坐标开启时，用另一套方式计算档位。**"

**关键的三条**：`Extend Lines Left/Right`（横向延伸是**显式开关**，默认关）、
`Additional Levels` 的**逐档位颜色/不透明度/自定义比例**、`Levels` 的**绝对值/百分比切换**。
这三条我们现在一条都没有。

TV 声称 fib retracement 支持 **24 档**（◐："allows you to set 24 different Fibonacci levels"）。
参考实现 `lwcd/src/core/specs.ts:32-57` 的 `FIB_LEVEL_DEFAULTS` 恰好 24 条（⬤ 逐条数过），
可视为该数字的源码级印证。

### 2.3 两种"时间斐波那契"的精确公式

这是用户最关心的一条（「我说的斐波那契是有时间的」）。TV 有**两个不同的工具**，公式不同：

#### (a) Fib time zone —— 2 点，乘斐波那契数列

- 两锚点 `p0`、`p1` 定义基准间隔 `unit = x(p1) − x(p0)`（**屏幕像素**，不是 bar 数）
- 第 k 条竖线位置：`x = x(p0) + unit × F_k`，`F = [0,1,2,3,5,8,13,21,34,55,89]`（◐ `43000518155` 原文列举了这串数）
- 默认 11 条，全部可见（⬤ `lwcd/src/core/specs.ts:157-168` 的 `FIB_TIMEZONE_LEVEL_DEFAULTS` 逐条核对）
- 有背景填充开关，填充在相邻两条竖线之间

#### (b) Trend-based fib time —— 3 点，线性系数

- 三点 `p0, p1, p2`；`i0/i1/i2` 是它们各自的 **bar 索引**
- 第 k 条竖线：`barIndex = round(i2 + coeff_k × (i1 − i0))`，再换回时间、换成 x
- 系数梯子 `coeff` = `[0, 0.382, 0.5, 0.618, 1, 1.382, 1.618, 2, 2.382, 2.618, 3]`（0.5 默认隐藏）
- 退化保护：`i0 === i1` 时返回空（没有可投影的时间跨度）

⬤ 源码位置：`lwcd/src/core/kinds/fib-time.ts:12-28`，核心一行是
`coords.barIndexToTime(Math.round(i2 + l.coeff * (i1 - i0)))`。

> **两者别混**：(a) 是 2 点、乘数列、像素间隔；(b) 是 3 点、乘线性系数、bar 索引间隔。
> 我们要做的「带时间的斐波那契」如果只做一种，应该先做 (a) —— 它才是 TV 工具栏里那个
> 标准「Fib time zone」，且 2 点交互与现有 fib 网格的操作完全一致，改造面最小。

---

## 三、参考项目对比

六个仓库全部 `git clone` 到本机、逐行读过（⬤）；license / stars / version 为 2026-10-21 `gh api` 实取（○）。

| 项目 | License | Stars | 版本 | 最后推送 | 工具数 | 横向延伸 | 逐档位样式 | 时间类 fib | 对象树 |
|---|---|---|---|---|---|---|---|---|---|
| **deepentropy/lightweight-charts-drawing** (`lwcd`) | MIT | 137 | 0.6.2 | 2026-10-15 | **86** | ✅ `extendLeft/Right` | ✅ `LevelDef` | ✅ 两种都有 | ✅ `group` 标签 |
| KlineChart (`eco/KLineChart`) | Apache-2.0 | 4,265 | 10.1.0 | 2026-10-21 | 16 overlay | ❌ 通栏 | ❌ | ❌ | ❌ |
| trading-vue-js (`C451`) | MIT | 2,305 | 1.0.2 | 2024-06-24 | 通用图元，无 fib | ❌ | ❌ | ❌ | ❌ |
| lightweight-charts-line-tools-core (`ltc`) | MPL-2.0 | 92 | 1.1.2 | 2026-07-16 | 全套 line tools | ✅ | ✅ | — | — |
| tvjs-xp (`tvjsx`) | MIT | 72 | 0.3.0 | 2023-05-13 | **0** | — | — | — | — |
| candlekit-charts | MIT | 56 | 0.2.1 | 2026-10-20 | 16 | ❌ | ❌ | ❌ | ❌ |

### 3.1 逐个判决

**`lwcd`（deepentropy）—— 唯一值得整体照搬架构的**

- `src/core/types.ts:452` `DrawingKind` —— **86 个成员的字符串联合**，按点数分节注释
  （1-point / 2-point / 3-point / 4-point / N-point / freehand）。
- `src/core/specs.ts:353` `OVERLAY_SPECS: Record<DrawingKind, OverlaySpec>` —— **86 个 key，与 union 一一对应**，
  每条带 `pointCount` / `preview` / `isBbox` / `hotkey` / `defaults`。
  **加一个工具的边际成本 = 加一条记录。** 这是 TV 工具表能廉价落地的唯一办法，别的项目都没有。
  > 计数陷阱：表里有一部分 key 不加引号（如 `pitchfan:`），用 `grep '"..."'` 会少数 23 个。
  > 正确数法是同时匹配 `"name":` 与 `name:`（见 §8 复核 #11）。
- `src/core/types.ts:370` `LevelDef = { coeff, color, visible, label?, width?, style?, text? }` ——
  逐档位样式就靠这个结构，不需要 `Record<number, …>` 这种脆弱设计。
- `src/core/scene/fib.ts:142-150`：`const left = s.extendLeft ? 0 : Math.min(a.x, b.x);`
  `const right = s.extendRight ? w : Math.max(a.x, b.x);` —— 与我们已经写好的 `hitTestScene`
  的 span 钳制（`drawing-scene.ts:196`）是同一套语义。
- `src/core/types.ts:613-621`：`visibility?: IntervalVisibility`（逐周期可见）、
  `fmt?: number`（点位含义迁移标记）、`group?: string`（对象树标签）。

**`ltc`（difurious）—— 唯一把交易逻辑挂到 fib 档位上的**

- `types.ts:213` `ExtendOptions { right, left }`，`:416` `FibRetracementLevel { coeff, color, opacity, … }`
- `ltc-fib/src/views/LineToolFibRetracementPaneView.ts:344-352` 是全生态里唯一把
  "延伸开关"落到绘制坐标的实现：
  ```
  options.extend.left  ? 0 : minScreenX
  options.extend.right ? paneDrawingWidth : maxScreenX
  ```
- `types.ts:428-461` `FibBracketOrder` + `FibRetracementTradeStrategy` —— **档位直接对应
  挂单/止损/止盈**。这一条对我们价值极高（selene 是交易平台，不是看盘工具），
  但**不在本轮范围**，记为后续候选。

**`candlekit` —— 反面教材**

- `DrawingPrimitive.ts:201,214`：`line(ctx, 0, y, W, y)` —— 硬编码全宽
- `DrawingController.ts:296-297`：FibRetracement 命中只看 `Math.abs(p.y - …) <= t`，
  **完全不检查 x 跨度**。同一个 bug 的绘制侧与命中侧各写一遍，两侧还都错了。
  这正是我们如果"改两个数字"会重蹈的坑。

**`KLineChart` —— 反面教材之二**

- `src/extension/overlay/fibonacciLine.ts:44-47`：`startX = 0; endX = bounding.width;`
  `percents = [1, 0.786, 0.618, 0.5, 0.382, 0.236, 0]` —— 通栏 + 只有 7 档、写死。
- 16 个 overlay 全是"线"，没有 fib 家族的扩展工具，没有时间类 fib。

**`trading-vue-js` + `tvjs-xp` —— 不可用**

- `trading-vue-js` 是通用图元库（Candles/Channel/LineTool/Range/…），**不含 fib 工具**；
  README 自标 `[Not Maintained]`，最后推送 2024-06。
- `tvjs-xp` 名字像扩展包，实际只有 chart-link / grid-resize / legend-buttons / settings-win，
  **零绘图工具**。别被名字骗了。

### 3.2 一条必须说清楚的更正

`lwcd` 的 scene 里确实有 `{ t: "hit", a, b, width }` 这类不可见图元
（`scene/fib.ts:58` 等），看起来像"几何唯一来源"。但**实测全仓 grep：没有任何消费者**
—— `runtime/scene-canvas.ts:80` 的 `case "hit":` 直接 return 跳过绘制，命中检测真正走的是
`core/kinds/hit-tests.ts:1028` 的 `hitTestKind()`（`fibHit` 在 `:111`）。

也就是说：lwcd 的"绘制与命中一致"不是靠共享数据实现的，而是靠 **`fibHit:117-118` 与
`sceneFib:145-146` 两处逐字重算同一个 `left`/`right` 表达式**（含同样的 `extendLeft/Right` 三元）。
这是一个**需要用注释锁死的重复**，不是我们想象的那种单一真源。

**结论**：可以照搬 `extendLeft/Right` 的语义与三元表达式，但别照搬"scene 里的 hit 图元"这套
——它在本仓是死代码。我们要么让绘制与命中真正共享一个 `{left,right}` 计算函数，
要么在两处都写同一条注释说明必须同步改。

---

## 四、我们现在的样子（逐条 file:line）

绘图模块总计 **2,445 行**（12 个文件），全部在 `apps/desktop/`。

### 4.1 缺陷一：档位线通栏（用户所说的「很反人类」）

`lib/chart/drawing-scene.ts:139-149`：

```ts
lines.push({
  gridId: grid.id,
  level,
  x0: 0,          // ← 142
  x1: width,      // ← 143
  ...
});
```

而同一函数在 `:116-119` 已经把两个锚点投影成了 `x0/x1`，只喂给了把手（`:127-128`）和腿（`:131`）。
**投影算出来的横向范围被丢掉了。**

三重证据：

1. **单测把 bug 锁死了** —— `lib/chart/drawing-scene.test.ts:33-38`，
   `it("每个档位一条横贯全宽的线")` → `expect(line.x0).toBe(0); expect(line.x1).toBe(800);`
2. **反证：命中侧其实是对的** —— `drawing-scene.ts:196`
   `if (x < Math.min(l.x0, l.x1) || x > Math.max(l.x0, l.x1)) continue;`
   这段 span 钳制写得没问题，只是被喂了 `0/width`，永远是 no-op。
   同一文件里 `test:174-178` 还专门测过它（手工把 `x0=300/x1=500` 再断言不命中）。
3. **标签贴在价格轴上** —— `:155` `x: width - labelRightPad`（`labelRightPad` 默认 6，见 `:103`），
   而 `paintLabel`（`drawing-canvas.ts:68-84`）用 `textAlign = "right"`，即文字右缘贴 `x`。
   `width` 来自 `chart.paneSize().width`（`chart-drawing-layer.tsx:147-148`）——
   **是含价格轴的整个窗格宽度**，不是绘图区宽度。所以档位数值现在压在价格刻度上。
   TV 的惯例是贴在线的**右端**（或按 Levels 设置放在线中/左）。

### 4.2 缺陷二：没有横向延伸开关

`lib/chart/fib-object.ts:38` 有 `autoExtend: boolean`，但它是**价格档位**的延伸
（把 1.272/1.414/1.618/2.0/2.618 加进 `levels`，见 `drawing-tree.ts:245-251`），
和 TV 的 `Extend Lines Left/Right`（**线的横向长度**）完全是两回事。
对象树里的文案「自动延伸（1.272 / 1.414 / 1.618 / 2.0 / 2.618）」
（`components/watch/object-tree-dock.tsx:517`）说得没错，但用户听到"延伸"就会想到通栏 ——
**术语撞车，必须改名**。

### 4.3 缺陷三：档位只是数字，装不下样式

- `fib-object.ts:36` `levels: readonly number[]` —— 一个比例数组，**没有任何位置放颜色/粗细/虚线/文字**。
- `fib-style.ts:24-29` `styleForLevel(level)` —— 硬编码 4 个桶：
  `0.5` 主色、`0.382|0.618` 次色、延伸位紫虚线、其余灰虚线。用户无法改。
- `fib-style.ts:35-40` `FIB_STYLE_SLOTS` —— 另一份同口径的桶定义，注释自己承认
  「matches 与 styleForLevel 必须同口径 —— 改一处必须改另一处」。**但实测全仓 grep
  （`apps/desktop/**/*.{ts,tsx}`，含测试）：`FIB_STYLE_SLOTS` 只有定义、零消费者。**
  渲染器 `drawing-canvas.ts:146` 是逐条线画、按线取色，从不按桶批量。
  所以这条"必须同步"的注释守的是一段死代码。
- 对象树只有勾选框：`object-tree-dock.tsx:521-535`（`ALL_LEVELS.map` → checkbox），
  没有颜色、没有比例输入框、没有不透明度。

### 4.4 缺陷四：只有一种工具

`lib/store/drawings.ts:57` `export type DrawingTool = "select" | "fib";`
`components/watch/drawing-tools.tsx`（56 行）就两个按钮：「斐波那契」和「对象 · N」。

`chart-drawing-layer.tsx:231-251` 的分支是 `tool === "fib" ? 开始拖拽 : 命中测试`，
**没有第三种可能**。要加趋势线/矩形/水平线，得改这个 if，而不是加一条注册表记录。

### 4.5 缺陷五：`timeframe` 是死字段

`fib-object.ts:44` 存了 `timeframe`，注释写「仅作标注，不参与几何计算」（这条没错），
但 TV 的语义是**逐周期可见性**（`43000484410`）。现在：

- `fib-object.ts:118-124` `isVisibleOnChart()` —— 只判 `visible` + `symbol`
- `lib/store/drawings.ts:191-193` `selectVisibleGrids()` —— 同样只判 `visible` + `symbol`
- 唯一的"使用"在 `lib/chart/tree-view.ts:44` 的搜索框 `grid.timeframe.includes(q)`

即：**存了、能搜、但不生效**。

### 4.6 缺陷六：对象树是平的

`lib/chart/drawing-tree.ts:17-21`：

```ts
export interface DrawingGroup { id: string; name: string; collapsed: boolean; }
```

没有 `parentId`。`FibGrid.groupId: string | null`（`fib-object.ts:32`）只能挂一层。
TV 的「无限嵌套 / 删组级联 / 空组自动清理」（`43000795451`）一个都没有。

### 4.7 缺陷七：`priceAtLevel` 只有线性

`fib-object.ts:55-61`：`startPrice + (endPrice - startPrice) * level`。
TV 的 `Fib levels based on log scale` 需要 `exp(ln(start) + (ln(end)-ln(start))·level)`。

### 4.8 缺陷八（隐蔽）：跨语言 canon 测试在本树静默跳过

`lib/chart/fib-levels.test.ts:29-32` 拼出 `packages/strategy-engine/selene_space_strategy/fib_corner.py`，
`:72` 用 `it.skipIf(!FIB_CORNER_EXISTS)`。

**`fib_corner.py` 只存在于 `yanjiu` 工作树，不在本树**（⬤ 实测 `ls` 不存在）。
所以本树的 162 个绘图测试里，那条"档位必须与策略侧一致"的测试是**跳过**的
（跑 `vitest run lib/chart/*.test.ts` 显示 `1 skipped`）。测试自己有 `:81-87` 的
`console.warn` 兜底，但 CI 不会因为 warning 变红。

---

## 五、改造清单（可直接派工）

按依赖顺序排列。每条给出：目标文件、行号、改什么、验收判据。
**P0 三条是这次"反人类"的直接病灶，其余是"模仿 TV"的扩展。**

### P0-1　档位线改为锚点线段

| 项 | 内容 |
|---|---|
| 文件 | `apps/desktop/lib/chart/drawing-scene.ts:139-149` |
| 改法 | 在循环外算 `const left = Math.min(x0, x1); const right = Math.max(x0, x1);`，`x0: left, x1: right`。`x0/x1` 来自 `:116-119` 已投影好的锚点，**不要再调一次 projector** |
| 文件 | `apps/desktop/lib/chart/drawing-scene.ts:155` |
| 改法 | 标签 `x: right - labelRightPad`（跟随线段右端）。**注意 `width` 是含价格轴的窗格宽度**（`chart-drawing-layer.tsx:147-148` 的 `paneSize().width`），所以贴 `width` 等于压在价格刻度上；贴 `right` 才落在线尾。若线段右端出画布，clamp 到 `width` |
| 文件 | `apps/desktop/lib/chart/drawing-scene.test.ts:33-38` |
| 改法 | 把 `it("每个档位一条横贯全宽的线")` 改为断言线段覆盖锚点：给定 `project` 里 start→x=100、end→x=400，则 `expect(line.x0).toBe(100); expect(line.x1).toBe(400);` |
| 文件 | `apps/desktop/lib/chart/drawing-scene.test.ts:174-178` |
| 改法 | 该测试现在手工改 `x0/x1` 来验钳制；改成**直接用真实锚点跨度**验（不再需要手工覆盖），否则测的是构造出来的假数据 |
| 验收 | 拖一条腿 → 档位线两端与两个锚点对齐；线外区域点击不选中（现在这个行为是"测了但没生效"，改完才真的生效） |
| 参考 | `lwcd/src/core/scene/fib.ts:145-146`；`ltc-fib/…PaneView.ts:344-352` |

> **为什么不是"改两个数字"**：`drawing-scene.ts:196` 的命中钳制已经写好，改完 `x0/x1`
> 它会立刻活过来。但只改绘制不改命中（或反之）就是 candlekit 的错误
> （`DrawingPrimitive.ts:201` 全宽 + `DrawingController.ts:296` 无 x 检查，两头都错）。
> 改完必须同时跑 `drawing-scene.test.ts` 的绘制与命中两组断言。

### P0-2　加 `extendLeft` / `extendRight`

| 项 | 内容 |
|---|---|
| 文件 | `apps/desktop/lib/chart/fib-object.ts:29-49`（`FibGrid`） |
| 改法 | 加 `extendLeft: boolean; extendRight: boolean;`（默认 `false`） |
| 文件 | `apps/desktop/lib/chart/drawing-scene.ts`（P0-1 的 `left/right` 处） |
| 改法 | `const left = grid.extendLeft ? 0 : Math.min(x0, x1);` `const right = grid.extendRight ? width : Math.max(x0, x1);` |
| 文件 | `apps/desktop/lib/chart/drawing-tree.ts:62-80`（`createGrid`）与 `:262-283`（`duplicateGrid`） |
| 改法 | 新建默认 `false`；复制沿用源值 |
| 文件 | `apps/desktop/lib/chart/drawing-persist.ts:14` 与 `:63-75`（`parseGrid`） |
| 改法 | `DRAWINGS_SCHEMA_VERSION` 1→2；`extendLeft: bool(o.extendLeft, false)`。旧数据（无字段）自动落 `false`，与 TV 默认一致，**不需要写迁移函数** |
| 文件 | `apps/desktop/components/watch/object-tree-dock.tsx:512-519` 附近 |
| 改法 | 在 `autoExtend` 勾选框旁加两个勾选：「向左延伸」「向右延伸」；**同时把 `autoExtend` 的文案从「自动延伸」改为「扩展档位（1.272…2.618）」**，消除术语撞车 |
| 验收 | 默认画出来是线段；勾"向右"→ 线延到画布右缘且命中区同步变长 |

### P0-3　档位样式可配置

| 项 | 内容 |
|---|---|
| 文件 | `apps/desktop/lib/chart/fib-object.ts`（新增类型） |
| 改法 | 新增 `export interface LevelOverride { color?: string; width?: number; dashed?: boolean; text?: string; }`，`FibGrid` 加 `levelStyles?: ReadonlyArray<{ coeff: number } & LevelOverride>` |
| 文件 | `apps/desktop/lib/chart/fib-style.ts:24-40` |
| 改法 | `styleForLevel(level, override?: LevelOverride)`：先取默认桶，再叠加 override。**顺手删掉 `FIB_STYLE_SLOTS`（`:35-40`）** —— 实测零消费者，纯死代码；留着它会让人以为改样式要同步改两处 |
| 文件 | `apps/desktop/lib/chart/drawing-scene.ts:138` |
| 改法 | `const style = styleForLevel(level, findOverride(grid, level));` |
| 文件 | `apps/desktop/lib/chart/drawing-persist.ts:46-76` |
| 改法 | 解析 `levelStyles`，逐项过滤非有限数/非字符串颜色；坏项丢弃（沿用现有"单条坏了不整包丢"策略） |
| 文件 | `apps/desktop/lib/chart/drawing-tree.ts`（新增） |
| 改法 | `setLevelStyle(tree, gridId, coeff, patch)` 返回新树；`resetLevels` 同时清 `levelStyles` |
| 文件 | `apps/desktop/components/watch/object-tree-dock.tsx:521-535` |
| 改法 | 每个档位行从「勾选框 + 文字」扩成「勾选框 + 档位数值 + `<input type="color">` + 粗细选择」 |
| 验收 | 给 0.618 单独设红色粗线 → 图上只有那一条变；刷新页面（localStorage 持久化）后仍在 |
| 参考 | `lwcd/src/core/types.ts:370-379` `LevelDef`；TV `43000518158` "Additional Levels" |

### P1-1　工具注册表

| 项 | 内容 |
|---|---|
| 文件 | `apps/desktop/lib/chart/tool-registry.ts`（新建） |
| 改法 | `export interface ToolSpec { kind: DrawingKind; label: string; pointCount: 1\|2\|3\|5\|7; preview: "line"\|"rect"\|"arc"; hotkey?: string; defaults: Partial<FibGrid>; }`，`export const TOOLS: Record<DrawingKind, ToolSpec>` |
| 文件 | `apps/desktop/lib/store/drawings.ts:57` |
| 改法 | `DrawingTool = "select" \| DrawingKind`，`DrawingKind` 从 registry 导入 |
| 文件 | `apps/desktop/components/watch/chart-drawing-layer.tsx:231-251` |
| 改法 | 把 `tool === "fib"` 的硬分支换成 `TOOLS[tool].pointCount` 驱动的多点采集状态机 |
| 文件 | `apps/desktop/components/watch/drawing-tools.tsx` |
| 改法 | 由 `TOOLS` 生成按钮（分组、快捷键 title） |
| 验收 | 加「水平线」= 只改 registry 一条记录 + 对应 scene 分支，不改 store/UI/layer 的 if |
| 参考 | `lwcd/src/core/specs.ts:353`（86 条）；这是全生态唯一有注册表的实现 |

### P1-2　时间类 fib（fib time zone）

| 项 | 内容 |
|---|---|
| 文件 | `apps/desktop/lib/chart/drawing-scene.ts`（新增 `SceneVLine`） |
| 改法 | 现有 `DrawingScene` 只有 `lines`（水平）、`labels`、`anchors`、`legs`。新增 `vlines: { gridId, level, x, y0, y1, color, dashed, size, dimmed }[]`，或把 `lines` 泛化为带 `axis: "h" \| "v"` |
| 文件 | `apps/desktop/lib/chart/drawing-canvas.ts:120-170` |
| 改法 | `paintScene` 加一段 vlines 绘制（顺序：腿 → 水平线 → 竖线 → 标签 → 锚点） |
| 文件 | `apps/desktop/lib/chart/drawing-scene.ts:180-212`（`hitTestScene`） |
| 改法 | 竖线命中：x 方向容差 + y 落在 `[y0,y1]` |
| 文件 | `apps/desktop/lib/chart/fib-levels.ts`（或新文件 `fib-time-levels.ts`） |
| 改法 | `FIB_TIME_SEQUENCE = [0,1,2,3,5,8,13,21,34,55,89]`。**注意：这是 TV 的时间口径，与 `HAND_DRAWN_LEVELS` 无关，不要并进 canon** |
| 几何 | `x_k = x(p0) + (x(p1) − x(p0)) × F_k`，像素空间，与周期无关 |
| 验收 | 画一条 2 点时间斐波那契 → 11 条竖线，间距按 1,1,2,3,5,8… 递增 |
| 参考 | `lwcd/src/core/scene/fib.ts:312-344`（`sceneFibTimeZone`） |

### P1-3　对象树嵌套

| 项 | 内容 |
|---|---|
| 文件 | `apps/desktop/lib/chart/drawing-tree.ts:17-21` |
| 改法 | `DrawingGroup` 加 `parentId: string \| null`；`removeGroup`（`:305`）改为**级联删子组与其下所有 grid**（TV 原文："Deleting a group deletes it with all its nested groups and drawings"），删空后父组的空子组自动清理 |
| 文件 | `apps/desktop/lib/chart/tree-view.ts:44` 附近（`groupRows`/`filterRowsByQuery`） |
| 改法 | 拍平成带 `depth` 的行序列，递归展开 |
| 文件 | `apps/desktop/components/watch/object-tree-dock.tsx:387-420`（分组行） |
| 改法 | 按 `depth` 缩进；拖拽入组（TV 支持拖到组标题上）可后置 |
| 验收 | 组套组套两层，选父组 → 所有后代对象进入选中集合；删父组 → 后代全消失 |
| 参考 | TV `43000795451`（◐）；`lwcd/src/core/types.ts:621` 只有扁平的 `group?: string`，**嵌套这一项 lwcd 也没做，无源码可抄** |

> **风险提示**：这是全清单里改动面最大的一条（涉及树模型、持久化 schema、渲染、
> 拖拽）。建议 P1-3 单独一个 PR，不要和 P0 混。

### P1-4　逐周期可见性

| 项 | 内容 |
|---|---|
| 文件 | `apps/desktop/lib/chart/fib-object.ts:44` 与 `:118-124` |
| 改法 | `timeframe: ChartInterval` → `visibility: ReadonlyArray<ChartInterval> \| null`（`null` = 全部周期可见，兼容旧数据） |
| 文件 | `apps/desktop/lib/store/drawings.ts:191-193` 与 `lib/chart/drawing-tree.ts:290-292` |
| 改法 | `selectVisibleGrids` / `visibleGrids` 增加 `interval` 参数并据此过滤 |
| 文件 | `apps/desktop/components/watch/kf-chart.tsx:1615` |
| 改法 | 传入当前 `tf` |
| 验收 | 同一对象在 1h 可见、在 4h 不可见（TV Visibility 页签行为） |
| 参考 | TV `43000484410`（◐）；`lwcd/src/core/types.ts:613` `visibility?: IntervalVisibility` |

### P2 组（价值明确、优先级低于上列）

| # | 项 | 落点 | 参考 |
|---|---|---|---|
| P2-1 | 对数刻度档位 | `fib-object.ts:55` `priceAtLevel` 加 `logScale` 分支（`exp(lerp(ln…))`，仅当 start/end > 0） | TV `43000518158`；`lwcd/src/core/scene/fib.ts` 的 `useLog` 分支 |
| P2-2 | Levels 绝对值/百分比 | `drawing-scene.ts:157` 标签文案；`FibGrid` 加 `levelsAsPercents: boolean` | TV 原文："You can set an absolute or percent value" |
| P2-3 | Reverse | `priceAtLevel` 入参翻转（`1−level`）或锚点交换 | TV `43000518158` |
| P2-4 | 档位间背景填充 | `drawing-canvas.ts:120` 加 `fills` 段。**`Ctx2D` 已暴露 `fillRect`（`drawing-canvas.ts:20`）与 `globalAlpha`（`:36`），无需扩接口** | `lwcd/src/core/scene/fib.ts:326-340`（`fillO > 0` 时画 rect） |
| P2-5 | Fib channel 四态 Extend 下拉 | P1-1 注册表 + 独立的 `extend: "none"\|"left"\|"right"\|"both"` | TV `43000791610`；`sceneFibChannel` |
| P2-6 | 趋势线 / 水平线 / 矩形 | 注册表第一批量产对象 | `lwcd/src/core/specs.ts` |

---

## 六、照搬 / 不照搬的分界

### 照搬（语义与数据结构，逐字对齐 TV 与 lwcd）

1. **`ExtendOptions { left, right }` 的语义**：默认 `false \| false` = 线段；显式开才到面板边缘。
   两处（`drawing-scene.ts` 绘制、`:196` 命中）用**同一个** `left/right` 计算。
2. **`LevelDef` 结构**：`{ coeff, color, visible, label?, width?, style?, text? }`。
   逐档位可配是 TV 反复出现的能力，不是扩展。
3. **工具注册表 + `pointCount` 驱动交互**：这是把 86 个工具从"86 次改 if"变成"86 行数据"的唯一手段。
4. **时间斐波那契的两个公式**（§2.3 原文）。
5. **Object Tree 的删组级联语义**：删组 = 删干净，不做"组内对象保留"。
   > ⚠️ 我们现在的 `removeGroup`（`drawing-tree.ts:305`）**就是"组内对象保留"** ——
   > 对象树按钮的 title 写着「删除分组（组内对象保留）」（`object-tree-dock.tsx:419`）。
   > 这与 TV 相反。改的时候要**显式决定**：改语义 + 改文案 + 改测试，三处一起。

### 不照搬

1. **candlekit / KLineChart 的"全宽 + 全宽命中"**：这两个是 bug 不是设计。
2. **`lwcd` scene 里的 `{t:"hit"}` 不可见图元**：本仓是死代码（§3.2 实测）。
   照搬会让我们多写一份永不执行的几何分支。
3. **TV 的 `Use one color` / `Background` / `Font size` / `Labels position` 等"模板级"旋钮**：
   在 P0–P1 阶段不实现。理由：这些是"锦上添花"，而线段几何与逐档位样式是"能不能用"。
4. **86 个工具一次性铺开**：先把注册表与 6 个基础工具（趋势线/水平线/竖线/矩形/价格区间/日期区间）
   跑通，再按用户实际使用频次排期。**不要为了"齐全"而齐全。**
5. **`ltc` 的 `FibBracketOrder`（fib 档位挂单）**：技术上有价值，但属于交易逻辑，
   会碰后端与 4 个汇聚点（见 CLAUDE.md），**不在绘图层 PR 里做**。

---

## 七、跨语言 canon（`fib_corner.py`）如何不受破坏

现状共三条约束，改造时必须同时满足：

### 7.1 档位族本身不能变

- `apps/desktop/lib/chart/fib-levels.ts:12-24` `HAND_DRAWN_LEVELS` =
  `[0, 0.2, 0.236, 0.3, 0.382, 0.5, 0.618, 0.7, 0.786, 0.886, 1]`（11 条）
- `packages/strategy-engine/selene_space_strategy/fib_corner.py:42-54` `FIB_LEVELS` = **逐值相同**（⬤ 已核对）

P0-3 引入的 `levelStyles` 是**样式叠加层**，`levels: readonly number[]` 保留不动 ——
所以 `HAND_DRAWN_LEVELS` 与 `fib_corner.py` 的对应关系不经过任何新结构，
**canon 不会被 schema 升级碰到**。

### 7.2 TV 的 24 档必须是"可加"，不能是"替换"

TV 默认可见的 11 档与我们不同的 4 条是哪些：

| 只在我们的 canon 里 | 只在 TV 默认可见里 |
|---|---|
| 0.2、0.3、0.7、0.886 | 1.618、2.618、3.618、4.236 |

策略侧 `fib_corner.py` 只做「价格贴近哪条内部线」的 touch 判定（`TOUCH_TOL = 0.003`），
**不消费 1.618+ 这些延伸位**（延伸位已在我们 `fib-levels.ts:34-36` 的 `EXTENSION_LEVELS` 单列，
注释写明"策略侧暂不消费"）。所以：

- TV 那套档位若要做，走 `EXTENSION_LEVELS` 这条已存在的旁路扩，
- **绝不往 `HAND_DRAWN_LEVELS` 里加数**，否则 `fib_corner.py` 的 touch 判定会多出策略侧
  没算过的档位，图与策略再次打架。

### 7.3 canon 测试现在**静默跳过**，必须改成响亮的

`fib-levels.test.ts:72` 的 `it.skipIf(!FIB_CORNER_EXISTS)` 在本树恒跳过（`fib_corner.py` 不存在）。

建议改法（不改测试语义，只改可见性）：

```
本树：console.warn 已有（:81-87），但 vitest 默认不把 warn 当失败。
      → 至少在 CI 里对 stdout 里的 `[fib-levels] 跳过正典比对` 做 grep 断红，
        或把该测试标成 `it.fails` 的显式"未验证"状态。
合入含 fib_corner.py 的分支：自动恢复逐值比对（现在就是这么设计的），无需改代码。
```

**这一条属于"当前的诚实度问题"，不是本次改造引入的。**

---

## 八、复核记录（US-006）

对至少 3 条关键结论回一手源复核：

| # | 断言 | 复核方式 | 结果 |
|---|---|---|---|
| 1 | TV fib retracement 支持 24 档、有 Extend Lines L/R、逐档位自定义比例+颜色+不透明度、Levels 可绝对值/百分比、对数刻度 | `WebFetch` 直取 `43000518158` 正文并逐句引用 | ✅ 原文五行全部命中 |
| 2 | TV fib time zone 的竖线落在斐波那契数列上，2 点 | `WebFetch` 直取 `43000518155`；序列原文 "Starting with 0 and 1,… (1+2=3, 2+3=5, 5+8=13, 8+13=21)" | ✅ 数字与实现 `FIB_TIMEZONE_LEVEL_DEFAULTS`（`lwcd/src/core/specs.ts:157-168`）逐条相同 |
| 3 | TV fib channel 有四态 Extend 下拉、可逐档位改比例与颜色 | `WebFetch` 直取 `43000791610` | ✅ "extend the channel lines indefinitely to the left, right, both, or not at all" |
| 4 | 我们的档位线通栏；命中侧已 span-aware 但被喂 0/width | 本机读 `drawing-scene.ts:139-149`、`:196`，并跑 `vitest run` 得 162 passed / 1 skipped | ✅ 代码与测试输出一致 |
| 5 | 六个参考项目的工具数/延伸/样式/时间 fib/对象树五个维度 | 本机 clone 逐文件读；license/stars 用 `gh api` 当轮实取 | ✅ 见 §3 表 |
| 6 | `lwcd` 的 `{t:"hit"}` 图元无消费者 | 全仓 grep `"hit"` → 仅 emitter + `scene-canvas.ts:80` 的 skip | ✅ 确认为死代码 |
| 7 | `fib_corner.py` 不在 draw 树、canon 测试跳过 | `ls packages/strategy-engine/…` → 不存在；`vitest` 输出 `1 skipped` | ✅ |
| 8 | `FIB_STYLE_SLOTS` 是无消费者的死代码 | 全仓 grep `FIB_STYLE_SLOTS` (`apps/desktop/**/*.{ts,tsx}`) → 仅 `fib-style.ts:35` 定义，零引用 | ✅ 修正了初稿"两处必须同步"的说法 |
| 9 | `removeGroup` 是"组内对象保留"，与 TV 相反 | 读 `drawing-tree.ts:303-311` 原文注释「组内对象**不删**,只是回到顶层。删分组顺手删掉用户画的线是灾难」；UI 文案在 `object-tree-dock.tsx:419` | ✅ 语义与文案一致，但与 TV 相反 —— 已在 §六 标为需显式拍板项 |
| 10 | `Ctx2D` 是否够画背景填充 | 读 `drawing-canvas.ts:13-38`：已有 `fillRect`(:20)、`fillStyle`(:34)、`globalAlpha`(:36) | ✅ P2-4 无需扩接口，已改 |
| 11 | `OVERLAY_SPECS` 到底多少条 | 两次独立计数：只匹配带引号键 → 63；同时匹配 `"k":` 与 `k:` → **86**，且与 `DrawingKind` 联合成员逐项相等（missing/extra 皆空） | ✅ 86 正确。**第一次数错是因为 23 条键未加引号**，该陷阱已写进 §3.1 |

**降级标注（报道级，未核实）**：

- TV 的 `Extend` 默认值到底是关还是开 —— 文档只说"可以选择"，没说默认。
  lwcd 的 `sceneFib:145` 用 `s.extendLeft ? …`、默认 `undefined → false`，可视为对默认值的推断，
  但**这是对 TV 的推断，不是 TV 官方声明**。
- fib wedge / pitchfan 是 2 点还是 3 点 —— TV 文档与 lwcd 源码冲突（§2.1），未解。
- TV 内部对 `fibLevelsBasedOnLogScale` 的具体算术（是 `exp(lerp(log))` 还是别的）——
  文档只说"alternative way"，未给公式。lwcd 的实现是推断。

### 8.1 独立复核（US-006）

初稿完成后另起一个独立 reviewer 会话，只读、不修改，回一手源抽查。
结论 **PASS**，抽查项与结果：

| 项 | 断言 | 复核源 | 结果 |
|---|---|---|---|
| A | `drawing-scene.ts` 档位线硬编码 `x0:0, x1:width`，而同函数的锚点投影只喂给把手与腿 | `drawing-scene.ts:116-150` | EXACT |
| B | `hitTestScene` 已做线段跨度钳制，但被喂 0/width | `drawing-scene.ts:180-215` | EXACT |
| C | `FIB_STYLE_SLOTS` 全 `apps/desktop` 零消费者 | grep 全目录 | EXACT |
| D | `priceAtLevel` 纯线性、无对数分支；`isVisibleOnChart` 只判 `visible`+`symbol` | `fib-object.ts` 全文 | EXACT |
| E | TV 文章 `43000518158` 载有 Extend Lines L/R 与逐档位自定义比例/颜色/不透明度 | `/d/tmp/fibresearch2/txt/43000518158-…txt` | EXACT |

复核期间**我自己发现并修正了一处错误**：曾把 `OVERLAY_SPECS` 的键数写成 63
（用只匹配加引号键的正则导致少数 23 条），经二次全量计数确认 **86**，与 `DrawingKind`
联合成员一一对应；该陷阱已写进 §3.1 与复核 #11。

**仍未复核的三项**（见上方降级标注）不阻塞本清单，但实现时应以行为测试而非文档措辞为准。

### 8.2 落地记录（2026-10-22，P0-1 / P0-2 / P0-3 已实现）

三条 P0 全部实现并测试锁定。清单里的行号是**动手前**的，落地后已漂移 —— 下表给的是**实际位置**。

| 项 | 断言 | 落地位置 | 验证 |
|---|---|---|---|
| P0-1 | 档位线改为两锚点之间；标签贴线段右端并 clamp 到画布 | `lib/chart/drawing-scene.ts:143-144`（`lineLeft`/`lineRight` 提到循环外）、`:156-157`（绘制）、`:171`（标签 x） | 5 条几何断言；另 2 条命中跨度断言（线外点击不选中、`extendLeft` 后可命中） |
| P0-1 | 绘制与拾取同源 | 同一对 `lineLeft`/`lineRight` 同时喂 `drawing-scene.ts` 与 `hitTestScene` | 命中测试探针从 x=400 改到 x=150（线段内），否则测的是旧的全宽假设 |
| P0-2 | `extendLeft`/`extendRight` 进模型 | `fib-object.ts:67-69` | `FibGrid` 全部字面量补齐字段（6 个测试文件） |
| P0-2 | 解析端默认 `false`，v1 旧数据免迁移 | `drawing-persist.ts:21`（版本 2 → 语义上仍是 1 的解析器）、`:104` | 往返相等测试 + 「布尔字段缺省」测试 |
| P0-2 | 树操作 / store / UI 全链路 | `drawing-tree.ts:261`（`toggleExtendSide`）、`drawings.ts:104,178`（`toggleExtendSideOf`）、`object-tree-dock.tsx:531-551` | 4 条树测试 |
| P0-2 | 消除术语撞车 | `autoExtend` 文案改「扩展档位（1.272…2.618）」；新开关叫「横向延伸」 | `ui-text/drawing.ts` 同时登记两条 |
| P0-3 | 逐档样式类型 | `fib-object.ts:29-44`（`LevelOverride` / `LevelStyleEntry`）、`:84`（`levelStyles?`）、`:91`（`levelOverride`） | — |
| P0-3 | 默认桶 + 覆盖合并；删死代码 | `fib-style.ts:34-40`；`FIB_STYLE_SLOTS` 已删 | 8 条树测试（含"清空某项=回默认"） |
| P0-3 | 别名替换标签文字、价格保留 | `drawing-scene.ts:16`（`formatLabelText`）、`:173` | 断言 `label.text === "TP1  161.80"` |
| P0-3 | 坏项丢弃而非整包丢 | `drawing-persist.ts:54`（`parseLevelStyles`：hex 白名单 / 线宽夹取 / 24 字截断 / 按 coeff 去重） | 7 条持久化测试 |
| P0-3 | 往返相等不被空数组破坏 | `drawing-persist.ts:113-117`（无覆盖时不写该字段） | 旧数据 `parse(serialize(x))` 深等仍成立 |
| P0-3 | UI 逐档行 | `object-tree-dock.tsx:569-620`（颜色 / 线宽 / 别名 / 虚线）+ `globals.css` 新增 8 条规则 | — |

**验收口径**：绘图相关 9 个测试文件 **227 passed**；`npx tsc --noEmit` 退出码 0。

**全量套件**：`1917 passed / 1 skipped / 4 failed`，四条失败逐条查过，**均与本次改动无关**：

1. `lib/i18n/ui-text.test.tsx` —— 42 条缺译文全部来自 `components/watch/parts/ashare-screen-panel.tsx`，
   该文件是**未跟踪**的别窗 WIP（`git status` 显示 `??`）。本次新增的 6 条 dock 字符串**已在本轮补齐**并复核为零。
2. `components/shared/page-renderer-preload.test.ts`、`components/research/analysis-panel.test.tsx`、
   `components/research/question-surface.test.tsx` —— 三条单独跑**全绿**（3/3、10/10、29/29），
   是并行负载下的超时，不是断言失败。

**未做的**：P1 / P2 全部（tool registry、时间 fib、对象树嵌套、逐周期可见性）。
清单留在 §五，未动。

> 更新（同日）：P1-1 / P1-2 已落地，见 §8.4。P1-3 / P1-4 仍未动。

### 8.3 渲染实测证据（2026-10-22，P0 闭环）

§8.2 的表格是**静态**验证（读源码 + 单测）。但"档位线是通栏还是线段""开关真的动了像素吗"
这类判断，单测可以断言错宾语 —— 单测锁死的恰恰是本文件 §4.1 里那条被硬编码的全宽直线。
所以本节补**渲染实测**：真实 dev server（`:3002`，后端 `:8089`）+ 真实画布像素读数，
证据来自 canvas `getImageData` 逐行扫描，不是目测截图。

探针：`scratch/tmp/p0-render-probe.mjs`（注入 `__scan(hex)` / `__thickness(hex)` 逐行求目标色
的最小/最大 x，按跨度排序取最长行）。画布为 774 CSS px（DPR=1）。

| 断言 | 实测读数 | 判定 |
|---|---|---|
| P0-2 术语改名为「扩展档位」 | dock 文本 `扩展档位（1.272 / 1.414 / 1.618 / 2.0 / 2.618）` | ✅ |
| P0-2 「横向延伸·向右」真的延伸 | 0.5 档色行 `x0=62 x1=415 span=353` → `x0=62 x1=773 span=711`，画布宽 774；`grewByCss=358` | ✅ 延伸到右缘 |
| P0-3 逐档颜色真的换色且不重复 | 设 0.5 为 `#ff00ff` 后品红行落在**同一 y=484、同 span=353**；`oldPrimaryRows: 0`（金色线消失而非叠加） | ✅ |
| P0-3 逐档线宽真的加粗 | 线宽选 6：`solidRows 2 → 7`，`maxRowPixels` 保持 354（水平长度不变、垂向变粗，即水平线的正确读数） | ✅ |
| P0-3 别名 + 设置持久化 | localStorage：`{"schemaVersion":2,"levelStyles":[{"coeff":0.5,"color":"#ff00ff","width":6,"text":"TP1"}],"extendLeft":false,"extendRight":false,"autoExtend":false}` | ✅ |
| 英文态整块可读 | dock 按钮 `Objects · 1`；`Extension levels (…)` / `Extend horizontally` / `Left` / `Right` / `Per-level style (color / width / alias)` / `Levels` / `Reset levels` 全部命中，`cjkResidue: []` | ✅ |
| 英文态无障碍名 | aria-label：`Color of level 0.2` / `Line width of level 0.2` / `Alias of level 0.2` / `Level 0.2 dashed` | ✅ |

**一处探针假阴性，记录以免误读**：英文态检查里 `alias: false`。原因是探针用
`/\bAlias\b/`（大写 A）去匹配摘要行 `Per-level style (color / width / alias)`（小写 alias），
而同一轮的 aria-label `Alias of level 0.2` 证明翻译是活着的。**这是断言 artifact，不是缺陷。**

**环境侧的两处坑（非代码缺陷，记下来省下次的时间）**：

1. 页面 200 但停在启动动画 —— `dev-web-3002.bat` 把 `LOCAL_API_BASE` 指向 `:8089` 而当时没起后端，
   `/api/v1/health` 走 rewrite 拿 500，`backend-ready-provider` 的 `BYPASS_PATHS = ["/"]`
   只放行落地页，于是整个 `/watch/kf` 卡住不 hydrate。起 `dev-api-8089.bat` 后 ~11 秒恢复。
2. 各路由 500（`0xc0000142`，postcss worker 起不来）—— memwatch 的
   `LEAK count=30 worker="--experimental-import-meta-resolve" KILLED-TREE` 在构建中途杀了进程树，
   留下 604MB 半写入的 `.next`。停树 → 删 `.next` → 重启即恢复。
   复位脚本 `scratch/tmp/reset-kf-dev.ps1` 带 PID→命令行身份校验，防止误杀别的树的 3002。

### 8.4 落地记录（2026-10-22，P1-1 工具注册表 / P1-2 时间类 fib 已实现）

清单 §五 的 P1-1、P1-2 已实现并锁定。P1-3（对象树嵌套）、P1-4（逐周期可见性）**未动**，
理由见 §8.2 末尾与 P1-3 的风险提示（改动面最大，应单独 PR）。

**P1-1 工具注册表** —— 没有照清单原样建 `TOOLS: Record<DrawingKind, ToolSpec>` 的多点状态机。
实际的工具只有两种，多点采集（3/5/7 点）在本仓库没有任何消费者，为一个不存在的需求先搭状态机
属于过度设计。落地的是**能做单点判断的最小注册表**：

| 项 | 实际落地 | 验证 |
|---|---|---|
| 工具身份集中 | `lib/chart/tool-registry.ts`：`isDrawingTool()` 用 `Object.hasOwn` 判定，挡住 `"constructor"` / `"toString"` 这类原型链键 | 6 条断言（`tool-registry.test.ts`） |
| 新增 `fibTime` | 与 `fib` 同源，`mode: "time"` 由 registry 的 `defaults` 给出，layer 里没有 `tool === "fib"` 的新分支 | 见下 |
| store / UI | `DrawingTool = "select" \| DrawingKind`；按钮由 registry 生成 | 时间斐波那契按钮在实测中存在且 `aria-pressed` 正确翻转 |

**P1-2 时间类 fib** —— 清单说「新增 `SceneVLine` 或把 `lines` 泛化成 `axis: "h" \| "v"`」。
选了**前者**：水平线与竖线的字段（`y` vs `x`、`x0/x1` vs `y0/y1`）没有共享语义，
塞一个用不上的轴字段会让两边的读者都要多读一个 `if`。

| 项 | 实际落地 | 验证 |
|---|---|---|
| 场景 | `drawing-scene.ts:84-96`（`SceneVLine`）、`:101`（`vlines`）、`:175-209`（time 分支） | 13 条时间几何断言 |
| 几何 | `x_k = min(x0,x1) + \|x1−x0\| × k`，像素空间；反向腿也向右铺（`timeOffsetX`） | 反向腿断言 + 渲染实测 |
| 绘制顺序 | 腿 → 水平线 → **竖线** → 标签 → 锚点（`drawing-canvas.ts` `paintScene`） | rotate/translate 调用序列断言 |
| 竖排标签 | `save → translate(x,y) → rotate(−π/2) → fillText(0,0) → restore`；save/restore 计数相等 | 7 条 canvas 断言 |
| 竖线命中 | `hitTestScene` 对 vline 做 x 容差 + `y ∈ [y0,y1]` | 命中 `kind:"vline"` 且带 `level` |
| 档位族 | `fib-time-levels.ts`：`FIB_TIME_SEQUENCE = [1,2,3,5,8,13,21,34,55,89]`，**不与 `HAND_DRAWN_LEVELS` 合并** | 断言两者交集恰好 `[1]` |
| 持久化 | `DRAWINGS_SCHEMA_VERSION = 3`；缺省 `mode` 读成 `"price"`，v1/v2 存档零迁移 | 4 条 parse 断言 + 往返深等 |
| 默认档位 | 时间网格缺 `levels` 时回落**时间倍数族**，不是价格比例族 | 断言不含 `0.5` |

**一处与清单的偏离**：清单写 `FIB_TIME_SEQUENCE = [0,1,2,3,5,8,13,21,34,55,89]`（11 档），
落地去掉了 `0`。`k=0` 落在腿的起点上，与锚点重叠、不可区分，且 TV 的时间位本身也从第 1 个
斐波那契数起算。因此实测探针期望的是 4 条（画 80px 腿时 `2/3/5/8` 落在画布内，其余出界）。

#### 渲染实测证据（P1-2）

探针 `scratch/tmp/reloadprobe.mjs` / `scratch/tmp/colprobe.mjs`：清 localStorage → 刷新 → 点
「时间斐波那契」→ 在 `.chart-main` 上拖一条 **80px** 水平腿 → 对 `canvas[aria-hidden=true]`
逐像素统计每列/每行的不透明像素数。画布 1042×708 CSS px（DPR=1）。

| 断言 | 实测读数 | 判定 |
|---|---|---|
| 拖着画出来的确实是时间网格 | localStorage：`{"version":3,"grids":[{"mode":"time","levels":[2,3,5,8,13,21,34,55,89],...}]}` | ✅ |
| 竖线落在 `left + span×k` | 锚点左端 x=385、腿长 80 → 期望 `545/625/785/1025`；实测列 `545,546 / 625,626 / 785,786 / 1025`（2px 宽，抗锯齿分两列） | ✅ 四条逐点吻合 |
| 竖线上下贯通 | 每条高列的 `first=0`、`last=707`（=画布高 708） | ✅ |
| 竖线是虚线（不是断头） | 列剖面 `1111....1111....`，占空比 4:4；列像素数 362 ≈ 708×0.51 | ✅ |
| **没有**价格网格那种全宽横线 | 全画布最长的一行只有 58px = 1042 的 **5.6%**（那是竖排标签的文字，不是线）；价格网格的读数应是接近 100% 宽 | ✅ |
| 刷新后从存档恢复并重投影 | 刷新后 2s 无、4s 出 1 条、8s 出全 4 条，位置随视口重算为 `684/741/856/1029`（**不是**存档里的旧像素） | ✅ |

**一处探针假阴性，记录以免误读**：第一版探针用 `cols[x] > H*0.8`（即 566px）判定"整列"，
结果 `gotVCols: []`，一度被读成"竖线没画出来"。真因是竖线**默认是虚线**
（`fib-style.ts:48` 的三级档位 `dashed: true`），占空比 51% 永远够不到 80% 阈值。
放宽到 `>300` 后四列立刻现身。**这是阈值 artifact，不是缺陷。**

**另一处非缺陷**：刷新后 2 秒内画布为空，是因为框架坐标尚未就绪、`xOfTime` 返回 `null`，
`buildDrawingScene` 按设计**跳过整条网格**而不是画到 `(0,0)`（`drawing-scene.ts:164`）。
数据到位后自动出现，无需用户操作。

**验收口径**：`npx vitest run lib/chart lib/store/drawings` → **16 files / 363 passed / 1 skipped**；
`npx tsc --noEmit` 退出码 0。

### 8.5 落地记录（2026-10-22，P2-6 基础图形：趋势线 / 水平线 / 竖线 / 矩形）

清单 §五 的 P2-6 写的是「趋势线 / 水平线 / 矩形」，落地补了第四个**竖线** ——
TV 的基础图形组里它是水平线的对偶，两个单点工具共用同一条 `pointCount: 1` 交互路径，
拆开做反而是多写一遍。

**没有为这四种图形新建点模型。** `FibGrid` 的两个锚点继续装下全部六种模式，
端点在各模式里被重新解释：水平线只读 `end.price`（忽略时间），竖线只读 `end.time`
（忽略价格），矩形把两锚点当对角线，趋势线直接连线。这样做是因为交互、命中、
拖动、持久化、撤销全部已经按「两个锚点」写好 —— 换一套点模型等于把这五层各重写一遍。

| 项 | 实际落地 | 验证 |
|---|---|---|
| 工具身份 | `tool-registry.ts` 的 `TOOLS` 从 2 条扩到 6 条，新增 `pointCount: 1 \| 2` 与 `group: "fib" \| "basic"` | 注册表断言覆盖 6 条 + 分组切片 |
| 落笔判据 | `chart-drawing-layer.tsx`：`pointCount === 1` 单击即建（拖拽也接受，松手处即该点）；两点工具保留「至少隔 1 秒」的误触判据 | 单点/两点各一组交互断言 |
| 单点落笔 | `store/drawings.ts` 的 `createFromDrag` 把两锚点压成同一点 —— 否则拖一下就会留下一条几何根本用不到的腿 | 断言 `start === end` |
| 趋势线几何 | 新增 `SceneSegment`（x0/y0/x1/y1 四个数）与 `distanceToSegment()` 做点—线段距离命中；不能只看纵向差 | 斜线命中断言 |
| 矩形几何 | 两条水平边进 `lines`、两条竖边进 `vlines` —— **命中检测零改动**，复用已有的横线/竖线分支 | 四条边逐条命中断言 |
| 把手 | 单点工具只画一个把手（index=1）；两点工具两个 | 场景断言 |
| 档位语义 | `hasLevels(mode)` 只对 `price` / `time` 为真；四种基础图形 `levels` 恒空，且**不回落到 trader 档位族**（否则一条趋势线会凭空长出 9 条水平线） | 解析断言 + 绘制断言 |
| 对象树面板 | 档位 / 扩展档位 / 逐档样式三块只在 `hasLevels(g.mode)` 时渲染；行标题由 `toolLabelForMode(mode)` 反查 | 实测见下表「对象树行」 |
| 工具栏 | 从两个平铺按钮改为分组下拉（`.chart-toolbar` 高 28px、不换行，`.chart-main` 又 `overflow:hidden`，6 个工具平铺会被裁尾）。弹层走 portal + fixed，与 `.kf-ind-picker` 同一套外壳 | 锚点几何实测见下表 |
| 持久化 | `DRAWINGS_SCHEMA_VERSION` 3 → 4；未知 `mode` 读成 `"price"`，旧存档零迁移 | 往返深等断言 |

#### 渲染实测证据（P2-6）

探针 `scratch/tmp/p26-render-probe.mjs`（配套 `scratch/tmp/p26-menu-diag.mjs` 量菜单锚点）。
清 localStorage → 刷新 → 从下拉里选工具 → 在 `.chart-main` 上拖拽（趋势线 / 矩形，
14 个插值移动点）或单击（水平线 / 竖线）→ 对 `canvas[aria-hidden=true]` 逐像素统计
非透明像素数与包围盒。画布 **1042×708 CSS px（DPR=1）**。

| 断言 | 实测读数 | 判定 |
|---|---|---|
| 起始画布是空的（防「空图层也报有像素」的假阳性） | `paintedPx = 0`，`bboxCss: null` | ✅ 基线干净 |
| 趋势线是真线段而非通栏 | `paintedPx 1534`，bbox `x 284→678, y 266→529`（394×263，与拖拽起止点吻合） | ✅ |
| 水平线横贯整幅 | `paintedPx 2812`，bbox `x 0→1041`（画布宽 1042）、`y 448→469` | ✅ 左缘到右缘 |
| 竖线上下贯通 | `paintedPx 1542`，bbox `y 0→707`（画布高 708）、`x 634→648` | ✅ 顶到顶 |
| 矩形是四边而非实心 | `paintedPx 2731` 对 394×253 = 99 682 px 的内接面积，覆盖率 **2.7%** | ✅ 描边 |
| 画的是基础几何、**不是**档位线循环 | 主色全部是 `#7aa2f7` 的 12~24 个抗锯齿变体（`122,162,247` 在趋势线占 249px、水平线 `121,162,247` 占 2056px）；斐波那契档位色 `#e3b650` 只在竖线/矩形里出现 **8~15 px**，那是两个锚点把手 | ✅ `levels: []` 与画面一致 |
| 下拉含全部工具、分两组 | 7 个 `menuitemradio`：选择 / 斐波那契 / 时间斐波那契 / 趋势线 / 水平线 / 竖线 / 矩形；分组标签 `["斐波那契","基础图形"]` | ✅ |
| 选中态只有一个 | `aria-checked=true` 仅「矩形」 | ✅ |
| 弹层贴住触发按钮 | trigger `{left:486, bottom:82}` → menu `{left:486, top:86}`（+4px 间距，与 `place()` 一致） | ✅ 无错位 |
| 画完对象树出现对应行 | 行尾 `矩形` —— 由 `toolLabelForMode` 反查，不是硬编码 | ✅ |
| 触发按钮回显当前工具 | 选「趋势线」后按钮文本 `趋势线 ▾` | ✅ |

四张截图（`p26-trend.png` / `p26-horizontal.png` / `p26-vertical.png` / `p26-rect.png`）
已逐张打开目视确认：趋势线为斜线、水平线贯穿左右、竖线贯穿上下、矩形为空心框；
`p26-menu.png` 确认下拉分组与选中高亮。**目视只作几何复核，数值结论全部来自上表的像素读数。**

**验收口径**：`npx vitest run lib/chart lib/store/drawings components/watch/drawing-tools.test.tsx`
→ **17 files / 410 passed / 1 skipped**；`npx tsc --noEmit` 退出码 0。

---

## 附：本文未覆盖、但值得记下的东西

- **`ltc` 把 fib 档位与挂单绑定**（`FibBracketOrder`）：对 selene 这种"绘图驱动交易"的产品
  是天然契合点。但会碰后端 schema 与 4 个汇聚点，单独立项。
- **TV 的 Displacement 页签支持对价格做 `× ÷`**：如果我们做多选批量位移，
  这是现成的交互规格。
- **"Keep drawing" 模式**：TV 画完一个工具自动回到光标，除非勾了 keep drawing。
  我们的 `activeTool` 当前画完一次是否回 `select`，需在 P1-1 一并决定。
