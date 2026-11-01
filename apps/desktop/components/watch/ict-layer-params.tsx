"use client";

// 对象树里 ICT 四层的**参数抽屉** —— 展开每一行指标时显示。
//
// 为什么放在树里而不是再开一个弹层:
//   树的展开行本来就是「这个对象有哪些可调的」的位置,绘图对象已经这么用
//   (档位/样式/延伸)。ICT 再冒出一个浮层会让"开关在树里、参数在别处",
//   用户得记住两套位置。
//
// 每个控件都直接写 store(改动立刻上图),不设"应用/取消":
//   与绘图设置面板同口径 —— 这是一个看得见效果的东西,预览即结果。
//   参数落 localStorage(见 lib/store/ict-display.ts),跨会话保留。

import { useUiText } from "@/components/i18n/use-ui-text";
import { useIctDisplayStore } from "@/lib/store/ict-display";

type LayerKey = "swings" | "events" | "killzone" | "dealingRange";

/** 数字输入:受控于 store,失焦时按后端约束收敛(store 的 parse 也会兜一次)。 */
function NumberField({
  label,
  value,
  min,
  max,
  step = 1,
  onChange,
  suffix,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step?: number;
  onChange: (v: number) => void;
  suffix?: string;
}) {
  const tr = useUiText();
  return (
    <label className="kf-draw-field">
      <span className="kf-draw-label">{tr(label)}</span>
      <input
        className="kf-draw-input"
        type="number"
        min={min}
        max={max}
        step={step}
        value={value}
        aria-label={tr(label)}
        onChange={(e) => {
          const n = Number(e.target.value);
          if (Number.isFinite(n)) onChange(Math.min(max, Math.max(min, n)));
        }}
      />
      {suffix && <span className="kf-draw-range">{suffix}</span>}
    </label>
  );
}

function CheckField({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  const tr = useUiText();
  return (
    <label className="kf-draw-check">
      <input
        type="checkbox"
        checked={checked}
        aria-label={tr(label)}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span>{tr(label)}</span>
    </label>
  );
}

/** 三选一。用 radio 语义而不是下拉 —— 选项少、需要一眼看到当前值。 */
function SegField<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: readonly { value: T; label: string }[];
  onChange: (v: T) => void;
}) {
  const tr = useUiText();
  return (
    <div className="kf-draw-field">
      <span className="kf-draw-label">{tr(label)}</span>
      <div className="kf-draw-seg" role="radiogroup" aria-label={tr(label)} style={{ margin: 0 }}>
        {options.map((o) => (
          <button
            key={o.value}
            type="button"
            role="radio"
            aria-checked={value === o.value}
            className={`kf-draw-seg-btn ${value === o.value ? "active" : ""}`}
            onClick={() => onChange(o.value)}
          >
            {tr(o.label)}
          </button>
        ))}
      </div>
    </div>
  );
}

function SwingsParams() {
  const settings = useIctDisplayStore((s) => s.settings.swings);
  const setSwings = useIctDisplayStore((s) => s.setSwings);
  const setTiers = useIctDisplayStore((s) => s.setSwingTiers);
  const tr = useUiText();
  return (
    <>
      <NumberField
        label="最多显示"
        value={settings.maxCount}
        min={5}
        max={200}
        onChange={(v) => setSwings({ maxCount: v })}
        suffix={tr("个点")}
      />
      <div className="kf-draw-field">
        <span className="kf-draw-label">{tr("档位")}</span>
        <CheckField label="ST 短期" checked={settings.tiers.st} onChange={(v) => setTiers({ st: v })} />
        <CheckField label="IT 中期" checked={settings.tiers.it} onChange={(v) => setTiers({ it: v })} />
        <CheckField label="LT 长期" checked={settings.tiers.lt} onChange={(v) => setTiers({ lt: v })} />
      </div>
      <SegField
        label="方向"
        value={settings.side}
        options={[
          { value: "both", label: "两边" },
          { value: "high", label: "仅高点" },
          { value: "low", label: "仅低点" },
        ]}
        onChange={(v) => setSwings({ side: v })}
      />
      <div className="kf-draw-group-note">{tr("超出上限时优先保留近端与高档位")}</div>
    </>
  );
}

function EventsParams() {
  const settings = useIctDisplayStore((s) => s.settings.events);
  const setEvents = useIctDisplayStore((s) => s.setEvents);
  const setKinds = useIctDisplayStore((s) => s.setEventKinds);
  const tr = useUiText();
  return (
    <>
      <SegField
        label="破位口径"
        value={settings.breakOn}
        options={[
          { value: "close", label: "收盘" },
          { value: "wick", label: "影线" },
        ]}
        onChange={(v) => setEvents({ breakOn: v })}
      />
      <div className="kf-draw-group-note">
        {tr("改口径会重新计算，图元可能整体位移")}
      </div>
      <NumberField
        label="最多显示"
        value={settings.maxCount}
        min={1}
        max={80}
        onChange={(v) => setEvents({ maxCount: v })}
        suffix={tr("条线")}
      />
      <div className="kf-draw-field">
        <span className="kf-draw-label">{tr("类型")}</span>
        <CheckField label="BOS" checked={settings.kinds.BOS} onChange={(v) => setKinds({ BOS: v })} />
        <CheckField label="MSS" checked={settings.kinds.MSS} onChange={(v) => setKinds({ MSS: v })} />
      </div>
      <SegField
        label="方向"
        value={settings.side}
        options={[
          { value: "both", label: "两边" },
          { value: "up", label: "向上" },
          { value: "down", label: "向下" },
        ]}
        onChange={(v) => setEvents({ side: v })}
      />
    </>
  );
}

function KillzoneParams() {
  const settings = useIctDisplayStore((s) => s.settings.killzone);
  const setKillzone = useIctDisplayStore((s) => s.setKillzone);
  const setSessions = useIctDisplayStore((s) => s.setKillzoneSessions);
  const tr = useUiText();
  return (
    <>
      <div className="kf-draw-field">
        <span className="kf-draw-label">{tr("时段")}</span>
        <CheckField
          label="伦敦"
          checked={settings.sessions.london}
          onChange={(v) => setSessions({ london: v })}
        />
        <CheckField
          label="纽约"
          checked={settings.sessions.new_york}
          onChange={(v) => setSessions({ new_york: v })}
        />
        <CheckField
          label="亚洲"
          checked={settings.sessions.asia}
          onChange={(v) => setSessions({ asia: v })}
        />
      </div>
      <NumberField
        label="时区"
        value={settings.tzOffsetHours}
        min={-12}
        max={14}
        onChange={(v) => setKillzone({ tzOffsetHours: v })}
        suffix={tr("小时")}
      />
      <NumberField
        label="不透明度"
        value={settings.opacity}
        min={0.05}
        max={0.4}
        step={0.01}
        onChange={(v) => setKillzone({ opacity: v })}
      />
      <div className="kf-draw-group-note">{tr("至少保留一个时段；纽约本地时间惯例为 -5")}</div>
    </>
  );
}

function DealingRangeParams() {
  const settings = useIctDisplayStore((s) => s.settings.dealingRange);
  const setDR = useIctDisplayStore((s) => s.setDealingRange);
  const setLines = useIctDisplayStore((s) => s.setDealingRangeLines);
  const tr = useUiText();
  return (
    <>
      <NumberField
        label="swing 半径"
        value={settings.period}
        min={2}
        max={100}
        onChange={(v) => setDR({ period: v })}
        suffix={tr("根")}
      />
      <NumberField
        label="溢价阈值"
        value={settings.premiumThreshold}
        min={0.51}
        max={0.99}
        step={0.01}
        onChange={(v) => setDR({ premiumThreshold: v })}
      />
      <NumberField
        label="折价阈值"
        value={settings.discountThreshold}
        min={0.01}
        max={0.49}
        step={0.01}
        onChange={(v) => setDR({ discountThreshold: v })}
      />
      <div className="kf-draw-field">
        <span className="kf-draw-label">{tr("显示")}</span>
        <CheckField
          label="溢价线"
          checked={settings.lines.premium}
          onChange={(v) => setLines({ premium: v })}
        />
        <CheckField
          label="均衡线"
          checked={settings.lines.equilibrium}
          onChange={(v) => setLines({ equilibrium: v })}
        />
        <CheckField
          label="折价线"
          checked={settings.lines.discount}
          onChange={(v) => setLines({ discount: v })}
        />
      </div>
    </>
  );
}

const RENDERERS: Record<LayerKey, () => React.JSX.Element> = {
  swings: SwingsParams,
  events: EventsParams,
  killzone: KillzoneParams,
  dealingRange: DealingRangeParams,
};

/**
 * 某一层的参数区。``layer`` 与对象树里的指标 key 对齐（见 KF_INDICATOR_STRUCTURE）。
 * 非 ICT 层传 null，调用方就不渲染这个组件。
 */
export function IctLayerParams({ layer }: { layer: LayerKey }) {
  const Renderer = RENDERERS[layer];
  return (
    <div data-testid={`ict-params-${layer}`}>
      <Renderer />
    </div>
  );
}

export type { LayerKey as IctLayerKey };
