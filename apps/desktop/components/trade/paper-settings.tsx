"use client";

import { useEffect, useState } from "react";
import { useApi } from "@/lib/hooks/use-api";
import { apiPost } from "@/lib/api-client";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { useAccountId } from "@/lib/hooks/use-account-id";
import { Loader2, Save, RotateCcw } from "lucide-react";
import { useUiText } from "@/components/i18n/use-ui-text"

// /api/v1/paper/settings?account_id= 返回 dict — {account_id, maker_fee_rate, taker_fee_rate}
interface PaperSettings {
  account_id?: string;
  maker_fee_rate?: number;
  taker_fee_rate?: number;
}

const DEFAULT_MAKER = 0.0002;
const DEFAULT_TAKER = 0.0005;

function toBps(v: string): number {
  const n = Number(v);
  return Number.isFinite(n) && n > 0 ? n : 0;
}

export function PaperSettingsPage() {
  const tr = useUiText()

  const accountId = useAccountId();
  const { data, isLoading, mutate } = useApi<PaperSettings>(
    `/api/v1/paper/settings?account_id=${accountId}`
  );

  const [maker, setMaker] = useState(String(DEFAULT_MAKER));
  const [taker, setTaker] = useState(String(DEFAULT_TAKER));
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  // 数据加载后同步表单(仅首次 / 数据变化)
  useEffect(() => {
    if (data) {
      setMaker(String(data.maker_fee_rate ?? DEFAULT_MAKER));
      setTaker(String(data.taker_fee_rate ?? DEFAULT_TAKER));
      setDirty(false);
    }
  }, [data]);

  function onChange(fn: (v: string) => void, v: string) {
    fn(v);
    setDirty(true);
    setSaved(false);
  }

  async function save() {
    const m = toBps(maker);
    const t = toBps(taker);
    if (!m || !t) return;
    setSaving(true);
    try {
      await apiPost(`/api/v1/paper/settings?account_id=${encodeURIComponent(accountId)}`, {
        maker_fee_rate: m,
        taker_fee_rate: t,
      });
      setDirty(false);
      setSaved(true);
      await mutate();
    } finally {
      setSaving(false);
    }
  }

  async function resetDefaults() {
    setMaker(String(DEFAULT_MAKER));
    setTaker(String(DEFAULT_TAKER));
    setDirty(true);
    setSaved(false);
  }

  const m = toBps(maker);
  const t = toBps(taker);
  const valid = m > 0 && t > 0;

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card title={tr("模拟设置")} right={<span className="font-mono text-ink-3">{accountId}</span>}>
        <div className="grid grid-cols-2 gap-2">
          <StatBlock label={tr("当前 Maker 费率")} value={`${(m * 100).toFixed(3)}%`} />
          <StatBlock label={tr("当前 Taker 费率")} value={`${(t * 100).toFixed(3)}%`} />
        </div>

        {isLoading ? (
          <div className="flex items-center justify-center py-6 mt-2"><Loader2 className="h-5 w-5 animate-spin text-muted-foreground" /></div>
        ) : (
          <>
            <div className="mt-3 space-y-2">
              <div className="flex items-center gap-2">
                <label htmlFor="maker-fee" className="w-32 text-[10px] text-ink-3">{tr("Maker 费率")}</label>
                <input
                  id="maker-fee"
                  value={maker}
                  onChange={(e) => onChange(setMaker, e.target.value)}
                  placeholder="0.0002"
                  className="w-36 rounded border border-border-soft bg-bg px-2 py-1 text-[10px] font-mono text-ink outline-none focus:border-gold/50"
                />
                <span className="text-[9px] text-ink-4">0.0002 = 0.02%(OKX VIP0 maker)</span>
              </div>
              <div className="flex items-center gap-2">
                <label htmlFor="taker-fee" className="w-32 text-[10px] text-ink-3">{tr("Taker 费率")}</label>
                <input
                  id="taker-fee"
                  value={taker}
                  onChange={(e) => onChange(setTaker, e.target.value)}
                  placeholder="0.0005"
                  className="w-36 rounded border border-border-soft bg-bg px-2 py-1 text-[10px] font-mono text-ink outline-none focus:border-gold/50"
                />
                <span className="text-[9px] text-ink-4">0.0005 = 0.05%(OKX VIP0 taker)</span>
              </div>
            </div>

            <div className="mt-3 flex items-center gap-2">
              <button
                onClick={save}
                disabled={!valid || saving || !dirty}
                className="flex items-center gap-1 rounded border border-gold/40 bg-gold/10 px-2 py-1 text-[10px] text-gold hover:bg-gold/20 disabled:opacity-40"
              >
                {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : <Save className="h-3 w-3" />}
                {tr("保存费率")}
              </button>
              <button
                onClick={resetDefaults}
                className="flex items-center gap-1 rounded border border-border-soft bg-surface-3 px-2 py-1 text-[10px] text-ink-2 hover:text-gold"
              >
                <RotateCcw className="h-3 w-3" />{tr("恢复默认")}
              </button>
              {saved && <span className="text-[10px] text-pos">{tr("已保存")}</span>}
              {!valid && <span className="text-[10px] text-neg">{tr("费率需 > 0")}</span>}
            </div>
          </>
        )}
      </V17Card>
    </div>
  );
}
