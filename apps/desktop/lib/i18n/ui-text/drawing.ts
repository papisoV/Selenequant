/**
 * drawing 域译文表 —— 手绘对象树相关组件(`components/watch/drawing-tools.tsx`)的 en 译文。
 *
 * 单独成域而不是塞进 watch.ts:那份已 800 行封顶,手绘是自成一体的子系统
 * (工具 / 对象树 / 档位面板),后续还会加对象类型,独立文件改动面最小。
 * 合并仍在 `ui-text/index.ts` 一处完成。
 *
 * ⚠️ 键是**中文源串**(含全角标点),须逐字符一致。
 */
export const DRAWING_TEXT_EN: Record<string, string> = {
  "斐波那契": "Fibonacci",
  "时间斐波那契": "Fib time",
  "时间斐波那契：拖出一条腿，按 1/2/3/5/8… 倍腿长画出竖线":
    "Fib time zones: drag a leg; verticals are placed at 1/2/3/5/8… × the leg's span",

  // ── 基础图形(绘图工具第 6 批) ──
  // 与斐波那契分两组:那一组有档位可调,这四个没有,混在一列里会让用户以为都能调。
  "趋势线": "Trend line",
  "水平线": "Horizontal line",
  "竖线": "Vertical line",
  "矩形": "Rectangle",
  "基础图形": "Basic shapes",
  "趋势线：拖出一条线段连接两点": "Trend line: drag from one point to another",
  "水平线：在某个价位单击，线横贯整屏（拖动可继续调整价位）":
    "Horizontal line: click a price; the line spans the chart (drag to adjust before releasing)",
  "竖线：在某个时间点单击，线自上而下贯通":
    "Vertical line: click a time; the line spans the chart top to bottom",
  "矩形：拖出对角两点圈定一个区域": "Rectangle: drag between two opposite corners",
  "绘图": "Draw",
  "绘图工具": "Drawing tools",
  "绘图工具：斐波那契 / 趋势线 / 水平线 / 竖线 / 矩形":
    "Drawing tools: Fibonacci / trend line / horizontal / vertical / rectangle",
  "选择": "Select",
  "点选/拖动已有对象": "Click or drag an existing object",
  "对象 ·": "Objects ·",
  "斐波那契网格：选中后在图上拖出一条腿（起点→终点）":
    "Fibonacci grid: with this active, drag a leg on the chart (start → end)",
  "对象树：显示/锁定/档位/分组": "Object tree: visibility / lock / levels / groups",
  "对象树": "Object Tree",
  "只看当前图": "Current chart only",
  // `全部标的` 已在 watch.ts 登记(US 研究宇宙卡片同用),不在此重复。
  "切换到：只看当前图": "Switch to: current chart only",
  "切换到：全部标的": "Switch to: all symbols",
  "全部标的的手绘对象 · 当前图 {symbol}": "Hand-drawn objects across all symbols · current chart {symbol}",
  "当前图 {symbol} 的手绘对象": "Hand-drawn objects on {symbol}",
  "还没有对象。点「斐波那契」后在图上拖出一条腿。":
    "No objects yet. Activate “Fibonacci”, then drag a leg on the chart.",
  "当前图上还没有对象。": "No objects on this chart yet.",
  "展开分组": "Expand group",
  "收起分组": "Collapse group",
  "删除分组（组内对象保留）": "Delete group (its objects are kept)",
  "隐藏": "Hide",
  "显示": "Show",
  "解锁": "Unlock",
  "锁定": "Lock",
  "收起档位": "Collapse levels",
  "展开档位": "Expand levels",
  "斐波那契网格": "Fibonacci grid",
  "未命名": "Untitled",
  "名称": "Name",
  "分组": "Group",
  "顶层": "Top level",
  // `扩展档位` 而不是 `自动延伸`:这个开关加的是**价格档位**(1.272…2.618),
  // 而下面「横向延伸」改的是档位线的**横向长度**(TradingView 的 extendLeft/
  // extendRight)。两者都叫"延伸"会让用户把两个复选框当成一件事。
  "扩展档位（1.272 / 1.414 / 1.618 / 2.0 / 2.618）":
    "Extension levels (1.272 / 1.414 / 1.618 / 2.0 / 2.618)",
  "横向延伸": "Extend horizontally",
  "向左": "Left",
  "向右": "Right",
  "单档样式（颜色 / 线宽 / 别名）": "Per-level style (color / width / alias)",
  "别名": "Alias",
  "档位 {label} 的颜色": "Color of level {label}",
  "档位 {label} 的线宽": "Line width of level {label}",
  "档位 {label} 的别名": "Alias of level {label}",
  "档位 {label} 虚线": "Level {label} dashed",
  "档位": "Levels",
  "恢复默认档位": "Reset levels",
  // ── 自定义档位(对象树:任意比例增删) ──
  "移除档位 {label}": "Remove level {label}",
  "比例，如 0.45": "Ratio, e.g. 0.45",
  "添加档位比例": "Ratio to add as a level",
  "添加档位": "Add level",
  "请输入比例": "Enter a ratio",
  "比例必须是数字": "Ratio must be a number",
  "该档位已存在": "That level already exists",
  // ── 绘图设置(kf 顶栏「设置」弹层):连续绘制 / 磁吸 ──
  "绘图设置": "Drawing settings",
  "绘图设置：连续绘制 / 磁吸": "Drawing settings: keep drawing / magnet",
  "绘制方式": "Placement",
  "连续绘制": "Keep drawing",
  "开启后画完一个对象工具不退出，可以接着画下一个":
    "When on, the tool stays active after an object is placed so you can draw the next one",
  "磁吸": "Magnet",
  "磁吸档位": "Magnet strength",
  "不吸附": "Off",
  "弱吸附": "Weak",
  "强吸附": "Strong",
  "落点自由，不吸附 K 线": "Free placement; no snapping to candles",
  "离 OHLC 50px 内才吸附": "Snaps only within 50px of an OHLC price",
  "总是吸附到最近的开/高/低/收": "Always snaps to the nearest open/high/low/close",
  "落点吸附到 K 线的开/高/低/收；只改价格，不改时间":
    "Snaps placement to a candle's open/high/low/close; changes the price only, never the time",
  "复制一份并向右下错开": "Duplicate, offset down-right",
  "复制": "Duplicate",
  "删除": "Delete",
  "新分组名": "New group name",
  "新建分组": "New group",

  // ── 指标区(合流进对象树后新增) ──
  // 区头刻意不写成 `指标`:该源串已在 research-b3b.ts 里译作 "Metrics"(那里是
  // 统计口径的「指标」),且本表在 index.ts 的展开序中**早于** b3b —— 写 `指标`
  // 会被后者覆盖,英文界面显示成 "Metrics"。这里用无歧义的源串。
  // 同理 `主图叠层` / `独立子窗格` 已在 watch.ts 登记(指标 picker 时代留下的),
  // 不在此重复 —— 重复会让「属哪个域」变含糊。
  "技术指标": "Indicators",
  "页面级 · 作用于全部图格": "Page-level · applies to every pane",
  "参数 {params}": "Params {params}",

  // ── 多选(第 3 步) ──
  // 用 `设为可见` / `设为隐藏` 而不是 `显示` / `隐藏`:批量按钮是**绝对值**语义
  // (选中集里一半可见一半隐藏时,点它必须全部变成同一个状态)。
  // 而 `显示` 在本表第 30 行已是 "Show"(单行开关的动词),且 watch.ts 里另有
  // `显示` = "Showing" 展开在后 —— 复用那个源串既撞义又显示成 "Showing"。
  "⌘/Ctrl 点选多项 · Shift 选区间": "⌘/Ctrl-click to multi-select · Shift for a range",
  "已选 {n}": "{n} selected",
  "清空选择": "Clear selection",
  "设为可见": "Show all",
  "设为隐藏": "Hide all",
  "绘图 {n} 项（{m} 个指标不参与显隐）": "{n} drawing(s) — {m} indicator(s) skipped",
  "隐藏选中的 {n} 项": "Hide {n} selected",
  "显示选中的 {n} 项": "Show {n} selected",
  "锁定选中的 {n} 项": "Lock {n} selected",
  "删除选中的 {n} 项": "Delete {n} selected",

  // ── 侧栏 / 搜索 / 组级开关(第 4 步) ──
  // `折叠成竖条` 的方案已否:入口只留工具栏的「对象 · N」,不再有第二个同名按钮。
  "收起对象树": "Collapse object tree",
  "搜索对象": "Search objects",
  "搜索对象（名称 / 标的 / 周期）": "Search objects (name / symbol / timeframe)",
  "清除搜索": "Clear search",
  "没有匹配「{q}」的对象。": "No objects match “{q}”.",
  "显示分组": "Show group",
  "隐藏分组": "Hide group",
  "锁定分组": "Lock group",
  "解锁分组": "Unlock group",

  // ── 几何图标工具栏(1022) ──
  // 工具名(斐波那契 / 趋势线 …)沿用上面的源串,图标按钮的 aria-label 就是它们;
  // 这里只补随改版新出现的手势提示:Delete 删除、Esc 逐级取消。
  "选择：点选/拖动已有对象（Esc 取消，Delete 删除）":
    "Select: click or drag an existing object (Esc cancels, Delete removes)",
  "删除选中的 {n} 项（Delete）": "Delete {n} selected (Delete)",
  "对象树：显示/锁定/档位/分组（{n} 个对象）":
    "Object tree: visibility / lock / levels / groups ({n} objects)",

  // ── 图表外观:背景 / 网格 / K 线配色 ──
  // 与「绘图设置」分属两件事:那边管怎么画线,这边管图表本身长什么样。
  "图表外观": "Chart appearance",
  "图表外观：背景 / 网格 / K 线颜色":
    "Chart appearance: background / grid / candle colors",
  "背景": "Background",
  "背景类型": "Background type",
  "透明": "Transparent",
  "纯色": "Solid",
  "背景色": "Background color",
  "透明跟随站点主题；纯色会把图表区域整块填满":
    "Transparent follows the site theme; a solid color fills the whole chart area",
  "显示网格线": "Show grid lines",
  "网格颜色": "Grid color",
  "K 线颜色": "Candle colors",
  // 主体 / 边框 / 影线三组 × 涨跌两色,与 TV「商品 → 颜色」页同口径。
  "上涨主体": "Up body",
  "下跌主体": "Down body",
  "上涨边框": "Up border",
  "下跌边框": "Down border",
  "上涨影线": "Up wick",
  "下跌影线": "Down wick",
}
