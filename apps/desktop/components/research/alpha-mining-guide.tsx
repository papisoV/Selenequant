"use client"

import { useState } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { HelpCircle, ChevronDown, ChevronRight } from "lucide-react"
import { useUiText } from "@/components/i18n/use-ui-text"

interface GuideTopic {
  id: string
  title: string
  body: string[]
}

const GUIDE_TOPICS: GuideTopic[] = [
  {
    id: "read-curve",
    title: "怎么看训练曲线",
    body: [
      "训练曲线是理解因子质量的第一扇窗。横轴是训练步数(step),纵轴是分数。",
      "面板上有两条线,性质完全不同。best_score 是「到目前为止出现过的最好成绩」的记录——它把训练全程的最高分持续钉在一个固定值上,只有当某一步真的刷新了纪录,这个值才会被新的纪录替换。所以这条线天然只会朝分数更高的方向移动,永远不可能回退。你可以把它想象成一条水位线:水涨上来就留在那个高度,不会自己退回去。",
      "avg_reward 则是「最近一个训练批次的平均奖励」——它反映的是当下这一小段探索的即时成绩。每一次训练步,模型都会随机生成一批公式、算一个平均分,这一批的表现有好有坏,所以这条线会跟着每一批的成绩上下摆动。摆动的幅度代表训练正在搜索的空间有多大:摆得宽,说明模型在广撒网试探不同的公式组合;摆得窄,说明模型基本锁定在某一类公式附近精调。",
      "如果你开启了 LoRD 正则化,还会看到一条稀疏的 stable_rank 虚线。它只在特定步数采样,数值越高说明公式的鲁棒性越好——也就是换一段数据、换一个随机种子,表现仍然稳定。",
      "什么时候该停?一个实用经验:best_score 这条水位线已经很久没有上涨,而 avg_reward 的摆动开始收窄、反复在同一片区域徘徊,说明模型能探索的新组合已经不多了。这时候继续跑,大概率只是在重复已经试过的公式,收益有限,可以停下来看结果。",
    ],
  },
  {
    id: "interval-scope",
    title: "为什么不能跨周期套策略",
    body: [
      "在 Selene 里,一个因子(公式)是在「某品种 + 某周期」的 klines 上训练出来的。它学到的是那个时间尺度上的规律。",
      "把 15m 上挖出的公式直接拿到 1d 上跑,等于把「分钟级的波动模式」当成「日线的趋势模式」来用——两者描述的是完全不同的市场行为。1d 的 K 线合并了 96 根 15m 的 K 线,噪声被平滑,反转节奏也完全不同。",
      "这也是为什么回测按钮按周期展示:每个已保存策略的配置档案(profile)都记录了自己的 base_interval,回测时用对应的周期去跑。想换周期,应该重新在那个周期上训练,而不是直接套用现成公式。",
      "数据源同理:训练时用的是真实本地 klines,覆盖范围以「根数 + 时间范围」显示在面板上。覆盖不足时训练会降级到合成数据——合成数据上的规律,尤其不能直接当真实市场的信号用。",
    ],
  },
  {
    id: "backtest-metrics",
    title: "回测的指标是怎么算的",
    body: [
      "回测会给你 8 个指标,它们来自同一份交易记录(每笔的盈亏、开平方向),只是看的角度不同。",
      "总收益(total_return)是把整段回测的净值变化直接算成百分比;年化收益(annual_return)把它按一年有多少根 K 线换算成年化——15m 一年约 35040 根,1h 约 8760 根,4h 约 2190 根,1d 约 365 根。周期不同,年化的换算倍数就不同。",
      "夏普(Sharpe)是「每承担一单位波动,赚到多少超额收益」,它的分母用的是全部波动(上涨和下跌都算);Sortino 只把下跌方向的波动算进分母,所以它更能反映「回撤时期」的真实代价。对趋势策略来说,Sortino 通常比 Sharpe 更能说明问题。",
      "最大回撤(max_drawdown)是净值从高点跌到低点的最深幅度,它告诉你最坏的情况下要扛多大浮亏。胜率和盈亏比描述交易本身的分布:胜率高但盈亏比低,往往是「赚小钱、频繁止盈」;胜率低但盈亏比高,则是「少数大赢家撑起整体」。两者结合才看得全。",
      "资金曲线(equity_curve)是净值逐笔的轨迹,滚动夏普(rolling_sharpe)是每 96 根 K 线为一个窗口、滑动半个窗口算一次夏普得到的序列——它比单点夏普更能看出收益的「稳定性」:如果滚动夏普长期在 0 附近甚至转负,说明这个策略只在少数时刻赚钱,这样的因子要谨慎部署。",
    ],
  },
]

function GuideItem({ topic }: { topic: GuideTopic }) {
  const tr = useUiText()
  const [open, setOpen] = useState(false)
  return (
    <div className="rounded-lg border border-border/70 overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between px-3 py-2.5 text-left text-sm font-medium hover:bg-muted/50"
      >
        <span>{tr(topic.title)}</span>
        {open ? <ChevronDown className="h-4 w-4 text-muted-foreground" /> : <ChevronRight className="h-4 w-4 text-muted-foreground" />}
      </button>
      {open && (
        <div className="space-y-2 px-3 pb-3">
          {topic.body.map((p, i) => (
            <p key={i} className="text-xs leading-relaxed text-muted-foreground">{tr(p)}</p>
          ))}
        </div>
      )}
    </div>
  )
}

export function AlphaMiningGuide() {
  const tr = useUiText()

  const [collapsed, setCollapsed] = useState(false)
  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <CardTitle className="text-base flex items-center gap-2">
            <HelpCircle className="h-4 w-4 text-sky-500" />
            {tr("新手引导")}
          </CardTitle>
          <Button variant="ghost" size="sm" className="h-7 gap-1 text-xs" onClick={() => setCollapsed((v) => !v)}>
            {collapsed ? tr("展开") : tr("收起")}
          </Button>
        </div>
      </CardHeader>
      {!collapsed && (
        <CardContent className="space-y-2">
          {GUIDE_TOPICS.map((t) => (
            <GuideItem key={t.id} topic={t} />
          ))}
        </CardContent>
      )}
    </Card>
  )
}
