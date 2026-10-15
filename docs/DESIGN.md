# Design

> 设计正典(2026-09-06 收官;2026-09-27 增第八章·交互原则)。从 Selene 重构设计讨论(0831-0906,七轮 GPT 对话+主会话收敛)定稿升格而来,与 [PRODUCT.md](PRODUCT.md) 配套:PRODUCT 定"是什么",DESIGN 定"长什么样、怎么长"。
> 规则:本文是规格非视觉稿——不写像素、不写示意数据。GPT 示例中的具体数字/指标名/策略 ID 一律不进规格,内容物留给真实数据。

## 一、产品定位

**AI-native Quant Research & Decision System = 持续运行的 AI Quant Organization。**

- 用户 = Research Director(研究总监),不是审批员。AI 自主研究,人管目标/方向/边界/关键决策
- 愿景压缩句(官方描述):"让一个个人投资者拥有一支持续工作的 AI 量化研究团队:它能读懂现实世界、理解我的策略、回到过去研究为什么成功或失败、实时判断当前环境,并通过实验和真实结果不断迭代自己的研究与策略。"
- 九条原始需求是一条回路,非九个功能:输入世界→理解市场/策略→回看过去/实时工作→驱动迭代/主动研究→沉淀记忆→人治理。验收 = 环转起来了吗,不是九个功能都有了吗
- 80% 研究 / 20% 执行:执行 = 研究的裁判节点(结论去真实市场受审),不是另一翼
- 注意力缩放律:一人注意力预算,系统总产出必须装得下;"没做什么"和"做了什么"一样重要
- 进度单位 = 已验证的知识(Memory 条目),非 PnL。领先指标:Memory 在长 + analogues 真被引用

## 二、楼层图(定稿)

```
                    SELENE
┌─────────────────────────────────────┐
│ TODAY  (orient)   注意力分配器        │
├───────────┬─────────────┬───────────┤
│ RESEARCH  │  MARKETS    │ STRATEGIES│
│(investigate)│(observe)  │(understand)│
├───────────┴─────────────┴───────────┤
│        RESEARCH MISSION (下钻层)     │
│  Evidence / Hypothesis / Experiment │
├─────────────────────────────────────┤
│ DECISION → PAPER/LIVE → OUTCOME      │
│ → MEMORY → 回 TODAY                 │
└─────────────────────────────────────┘
```

- 五页平级 = 五种工作对象,一页一动词;每页顶部一个灵魂问句
- 五页问句链:TODAY"现在什么值得我关注?"→ RESEARCH"团队在研究什么?"→ MISSION"这件事到底怎么回事?"→ STRATEGY"这对我的策略意味着什么?"→ MARKETS"现实世界发生了什么?"→ MEMORY"我们学到了什么?"↺
- MARKETS = Selene 对现实世界的持续观察窗口 + Work Generation 传感器(五页中唯一直接生产工作的窗)
- Strategy Execution 是 Strategy 的属性,不是一级产品对象;手工下单/运维进工具室
- 旧页面(工具室)降级:sidebar 只剩五页+⌘K;⌘K = Universal Command 进工具室;路由保留不删
- Trade/Watch 传统量化终端结构禁止长回顶层

## 三、三真实页面(垂直切片对象,全部定稿)

### TODAY — Attention Router

回答:"现在需要我吗?" 5 秒内。

```
LAST HANDOFF      三行:You did / Selene did / Current state(跨天连续性)
NEEDS YOU         E1 决策卡(视觉最重)
SELENE IS WORKING 进度行,粗相位五词表,诚实空态
WORLD CHANGED     变化行(视觉最轻)
```

- E1 决策卡四元:What happened / Why now / **Selene 已做了什么**(✓列表,治理接口——审阅已发生的工作再授权,防用户变盖章审批员)/ 你的决定
- Relevance = WORLD CHANGED 入场券,规则可算(策略 symbol 交叉/假设监控位/数据分位),禁 LLM 拍脑袋说"重要";无关联变化留 MARKETS;系统级变化(失联/数据陈旧)第二类入场券
- 视觉梯度固定:NEEDS YOU > WORKING > WORLD CHANGED,安静态自然塌缩
- 拍板发生在 Mission 页,v0 不内联(TODAY 纯只读投影,无生成物,没有说谎的器官)
- 后端:单聚合端点;B1 Mission 状态机(NEEDS_INPUT 是状态机属性)+ B2 全局活动流(append-only,记用户动作)+ B3 市场读数;与切片链路共账

### STRATEGY — Strategy Cockpit + Memory + Time Machine

回答:"这条策略现在还能不能信?"

```
头部   真实运行状态 + INTENDED/ACTUAL 并列(配置漂移可见)
NOW    Strategy Condition 定性行 + 当前信号/上下文 + [Investigate][Replay latest]
HISTORY History Ribbon(点阵生命轨迹,hover 真值,click 进 REPLAY)
REPLAY 全页时间机器(scrubber 拖 T,整页认知环境同步)
INTELLIGENCE 四桶知识 + Lineage
```

- 四窗不等权重,NOW 主视图;四窗 = 同一 Strategy Object 的四个观察深度,同一 Workspace 连续空间深度渐进(非四窗同屏)
- Strategy Condition 定性行(非 Health 分):Configuration / Evidence base / Recent performance / Market fit / Execution,每行可判真
- REPLAY v0 = **At-the-time snapshot**(无 LLM):信号触发条件(为什么触发)+ 当时认知环境双清单:RECORDED AT T(✓)vs NOT AVAILABLE THEN(✗)→ Reveal Outcome → post-mortem。防退化成"漂亮交易回放器"——核心差异不是 replay 本身,是恢复当时认知状态
- Temporal Integrity:回放历史绝不偷看未来。provenance 四档:RECORDED / RECONSTRUCTED / INFERRED / AFTER THE FACT
- INTELLIGENCE 四桶:KNOWN TO WORK / KNOWN FAILURE / UNCERTAIN / EXPERIMENTS;每条知识强制三件套:Why / Provenance(Source 哪些 Episodes/Missions/Experiments + Validation + Last observed)/ Outcome;点"为什么知道"直接跳溯源——防退化成"AI 自己总结的一句话"黑盒
- Investigate 处处有(Signal/Failure/Drift/Unknown Condition 四类入口)→ Research Mission;唯一性约束:同一对象+同一 unresolved question = 同一个 Mission(可做后端唯一性约束)

### MISSION — Decision Surface

回答:"这件事查清楚了吗?证据够不够?下一步干什么?"

- 定义:把当前事实、研究判断、证据、反方、可选下一步放在同一上下文中,让负责人能够作出决策。不是"AI 给建议→人盖章"
- 三栏 = 空间框架,状态驱动(Investigation 中:Findings|Active Work|Evidence;Validation 中:Hypothesis|Experiment|Evidence;Needs Input:Facts|Assessment|Evidence→Next);**Evidence 右锚恒定+三栏框恒定,只左中语义随状态变**(空间记忆+状态感)
- NEXT 四动作词表(研究负责人动作,防 Approval Queue Worker):Approve recommendation / Ask for more evidence / Redirect investigation / Park;按钮只从状态机合法转移来,禁推荐动作按钮大全
- 三件正典:①TODAY 卡 = Mission 页压缩投影(summary projection vs full decision surface,单对象单源)②NEXT 只从状态机转移(有生命周期的工作系统)③结案必须连锁反应(一次决策必须改变系统其他对象:Mission→Action→Experiment→Outcome→Memory→Strategy Intelligence→TODAY;若只是状态变 Complete 无意义)
- Activity Pulse v0 = 只读历史时间线;活形态(实时滚动)等 AI 后台持续干活自然长出
- MEMORY 页不施工 = 系统长期结果层,空态文案已定:"还没有已验证经验。第一条经验将在 Keltner Mission 完成真实 Outcome 后生成"

## 四、核心机制

- **Episode** = 最小经验单位(连接 Strategy/Replay/Mission/Memory):PRE-ENTRY → SIGNAL → PATH → EXIT → OUTCOME → POST-MORTEM
- **四维联合检索**(Market/Strategy/Execution/Event State)+ 正反例 analogues:问"成功与失败 episode 的决定性区别是什么"
- **因果五档**:Observed / Supported / Hypothesized / Validated / Unknown(Assessment 条目级标注)
- **演化循环**:七突变类型(Simplify/Change/Condition/Scope/Disable/Reweight/Risk;Risk 强制 risk-engine 仲裁 fail-closed);治理三分:AI 提出变化 / 实验证明变化 / 人授权变化;假设监控(策略携带 original_hypothesis + assumptions[],violation→自动生成 Research Work)
- **Lineage** = 研究版本控制 moat(Git for Quant Research);Keltner 修复 = 恢复声明意图的重验证,非 V3 新版本;Experimental Integrity 是 Lineage 守门员
- **Work Generation** 枢纽:Strategy 页 Investigate / MARKETS 传感器 / 假设监控 violation,三类工作出生地

## 五、军规(全楼强制)

1. **单对象单源**(Product-level invariant):同一对象只存一份,页面只持引用;Mission 在 TODAY/RESEARCH/STRATEGY/MEMORY 四处必须同源
2. **tile 三选一**:真数据 / 定性状态+缺口计数 / 不放——没有第四种(假数字)
3. **假精度分界**:可从现有数据计算 = 真;需要未造模型 = 禁(Health 92/Fit 73% 类)
4. **区块固定不重排**:Stable spatial grammar,空间记忆;内容呼吸,位置不动
5. **空必须显示为空**:诚实降级,"办公室很安静,因为一切在等你"是产品语言非缺陷;系统成长 = 空态逐渐变吵,这本身可见
6. **示意内容物不进规格**:GPT 例子的数字/指标名/策略 ID 是脚手架,结构真值从活动流/真实数据来
7. **每页一个问题**:页面顶部灵魂问句,比"一页一动词"更可执行

## 六、冷启动(1/1/1/1 真值)

- TODAY:1 张 NEEDS YOU 卡(Keltner REVALIDATE 决策)/ WORKING 诚实空 / WORLD CHANGED 真读数+交叉
- STRATEGY:7 个真 Episode(全亏但真)+ 配置漂移双列 + Lineage 起点 V2 + Intelligence 1 条真知识(漂移,三件套可填真值:Source=1 Mission / Validation=7 episodes / Last observed=2026-08-31)
- MISSION:四区全真零降级——WHAT(SOL 4H 7 笔全亏,声明 BTC 1H)/ ASSESSMENT(最强解释=配置漂移,不足以证明框架失效)/ EVIDENCE(配置 diff + bt-b62d32ac 回测 + trade records,三张对象卡)/ NEXT(REVALIDATE 四按钮)
- 主库:`~/.selenespace/data/selenespace.db`(全局,两副本共享)

## 七、下一步:Keltner 垂直切片(设计冻结后的唯一施工)

```
TODAY → STRATEGY(Investigate 深链) → MISSION(四区)
→ Approve REVALIDATE → Fix config → 真实回测 → OOS → 真实 Outcome
→ Memory 第一条 → Strategy Intelligence 更新 → TODAY 变行 ↺
```

- **严格不 mock**。施工顺序:链路先行(Keltner Mission 真跑,B1/B2 与链路共账)→ 聚合端点(B4)→ 前端挂载三页
- 验收标准 = 环转起来了吗:Memory 首条真生成 + Intelligence 真更新 + TODAY 真变行(非页面好看)
- 切片跑通前禁讨论:Strategy v2 / Market-driven Mission / 实时 LLM 怎么插
- 切片跑通后:第二条 Mission 从"AI infrastructure supply-chain tracker"原话长(独立于策略失效的研究线,防窄化成策略自愈系统)

---

## 八、交互原则(2026-09-27 增;来源:五命运判定 0925-0927 收敛 + 用户三项裁决)

> 与军规(§五)分工:军规管**内容诚实**(tile 三选一/假精度/空态),本章管**认知动线**——用户如何在页面之间移动、驻留、下钻、返回。两章同为全楼强制。
> 锚点参照(用户裁决):**终端为体 + 键盘为用**——信息密度与多面板监控学 TradingView/Bloomberg;到达路径与操作流学 Linear/Raycast。产品 register(earned familiarity):工具应消失在任务里,熟悉感是特性。

### 8.1 认知栈 = 五种认知深度(全楼布局机制)

UI 层级不是组件清单,是认知深度的投影。一切布局争论可翻译成"这东西在第几层"= 争论终止协议:

| 层 | 认知动作 | 布局机制 | 心智状态 |
|---|---|---|---|
| Context | 住 | hub 工作台 | 驻留并持续形成判断 |
| Panel | 盯 | 面板(hub 内) | 持续监控一个侧面 |
| Tab | 侧面 | tab(嵌入 hub) | 查一下,不离开 |
| Drawer | 钻 | 抽屉(任意层唤起) | 深入一个对象 |
| Fullscreen | 专注 | 独立全屏页 | 高度集中的复杂操作 |

- **Hub 准入 = 一句话测试**:hub 必须答出"用户在这里持续回答什么问题";五条辅助(持续观察对象/多判断侧面/反复下钻/明确 handoff/停留)。过不了 = panel/tab/tool,不是 hub。
- **Tab 是侧面不是堆场**:一个面的 tab 数有上限(数字 shape 批定);超限 = 面里混进了多个驻留问题,按测试拆分,不是继续加 tab。
- **Drawer 记账单位 = 对象类型**:按 ~10 类对象地址(symbol/策略/任务/公式/evidence/会话商品/账户等)建 drawer 机制,不是按页建;任何认知层可唤起。
- **Fullscreen 是仪式**:急停/复杂操作才配全屏;全屏不是"重要"的表达方式。

### 8.2 自由 = 到达自由(用户裁决,架构分水岭)

"灵活自由"落为**到达自由**:任意入口(⌘K / 深链 URL / drawer / 链)可到达任意对象,布局本身固定零新机制。布局自由(drag-drop 重排+持久化)不进 hub——那是 workspace 场景(§四 workspace 自管理)的职责,hub 抢它 = 职责重叠。

- **URL 可寻址 = drawer 前置**:每层认知状态入 URL(`?tab=derivatives&obj=strategy:xxx`),否则刷新丢 drawer、分享丢上下文。不可寻址的面 = 不存在。
- **⌘K = Universal Command**:所有 action 类能力(旧"操作页")的统一归宿;键盘优先是 Linear/Raycast 轨的落地形态。
- **军规④(区块固定不重排)在 hub 层升级**:空间记忆优先于个性化;要重排去 workspace。

### 8.3 导航答"我在哪",Question 答"我在干什么"

两条正交轴,不合并:

- **导航根 = 位置**(四场景 watch/research/trade/system + 链式分组):答"我在哪"。
- **Question = 上下文条**(第三机制,与 drawer 并列):答"我在干什么";不占导航根,挂当前 Mission/Question,跨 hub 驻留。资产上下文走既有 symbol store(useMarketStore 驱动面板绑定),零新机制。
- **Handoff = 横向连接**,是 Graph 的 UI 表达,不是"走"的层级;v0 只接已有外键边(Episode↔Strategy↔Question)。

### 8.4 删除的是认知地点,不是能力(Route Kill Rule)

杀任何 route 前五问,**任一答不上 = 不杀**:

1. 它不是独立研究上下文;
2. 它可以明确归宿到 Panel / Tab / Drawer / Action / Node;
3. 所有核心能力仍可被发现和调用;
4. 原有对象/研究状态可通过新表面恢复;
5. 不破坏 Deep Link / Agent / API 的语义。

- 旧页面 → 问"它真正是什么"(Context/Panel/Tab/Drawer/Action/Node/Dead),不问"删不删"。
- **"并"类铁律**:宿主 tab 先建或同批建,不许宿主未建先删 route(防信息空窗蒸发);action/node 同理——操作入口先落,再删页。
- **五命运判定表**(the route inventory)= 本章规则跑在全量路由上的产物;规则是资产,表是快照。

### 8.5 验收 = Context Switching Cost(指标换血)

"页面减少数"不是指标。标程:**走完"BTC 这波下跌该不该加仓"完整旅程——导航跳变 ≤1、对象不丢、无 mental model 重建。** Tab 降导航成本 ≠ 零认知成本,观察维度切换仍计。

### 8.6 交互微观律(product register 落地)

- **组件七态**:每个交互组件 default/hover/focus/active/disabled/loading/error 齐;加载用 skeleton 不用居中 spinner;空态教界面(与军规⑤"空必须显示为空"同源)。
- **Motion 传达状态**:150-250ms;状态变化/反馈/加载/揭示之外无动效;无页面加载编排序列(产品载入即任务)。
- **Modal 是最后手段**:先穷尽 inline/渐进展开,再考虑弹层。
- **语义色纪律**:涨跌用 pos/neg utility(zh-CN 红涨绿跌翻转),accent 只用于主操作/当前选中/状态指示,禁装饰性用色。
- **密度是许可**:终端为体——表格多行/面板多标签/紧凑信息是产品权利;但每页一个问题(军规⑦)与视觉梯度(TODAY 三区)照常生效。

### 8.7 终端投影(2026-09-27 增;终端 = 一等公民)

**GUI 和终端 = 同一认知栈的两个投影。** 每一层认知栈都该有终端形态;两个投影共享同一份寻址方案、对象词汇表、命令注册表。终端操控 selene(selenespace ask)不是"附加支持",是正典要求:

| 认知栈 | GUI 投影 | 终端投影 |
|---|---|---|
| Context(住) | hub 工作台 | ask 会话的当前上下文 |
| Tab(侧面) | hub tab | 寻址参数 `?tab=` |
| Drawer(钻) | 对象抽屉 | `obj=` 寻址 + ~10 类对象 API(同词汇表) |
| Action | ⌘K 命令 | 同一注册表,CLI 直调 |
| Fullscreen(专注) | 全屏页 | 交互式 TUI(急停类仪式) |

- **三个契约**(GUI/终端共同遵守,单方违反 = 回炉):①寻址方案 = 共同契约(§8.2 的 URL 规则对终端同样生效) ②⌘K 命令注册表 = ask 可调用注册表——**一份数据,两个消费端**;只在 cmdk-panel 注册的 action = 终端丢失该能力,违规 ③对象词汇表 = ~10 类对象地址,GUI drawer 与终端对象 API 同名同义。
- **验收对称**:CSC 标程(§8.5)走双投影——人走一遍,ask 走一遍;两个投影都能 ≤1 跳变完成,设计闭环。
- 零新机制:ask CLI(0921 拍板终端 = 2×2 第四格,自建不集成 Claude Code)/ ⌘K / URL / 对象类全部已有;本节 = 把已有构件声明为契约。

---

*定稿来源:0831 定位弧线 → 0905 三份体验层文档(World Model/时间机器/演化循环)→ 0906 楼层图+三页逐页裁决(第四至八轮评审)。§八来源:五命运判定(0925-0927,全量路由盘点)+ 两轮评审 + 用户裁决(锚点参照/到达自由/落点三项)。*
