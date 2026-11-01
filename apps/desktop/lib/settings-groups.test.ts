import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import {
  ADVANCED_GROUPS,
  DEDICATED_PANEL_GROUPS,
  DEDICATED_PANEL_KEYS,
  GROUP_LABELS,
  READONLY_GROUP_ORDER,
  filterReadonlyEntries,
  humanizeSettingKey,
  settingsGroupLabel,
  sortGroupKeys,
} from "./settings-groups";
import { TIERED_TRAILING_DEFAULTS } from "./api/settings";

/**
 * 覆盖锁:后端每多一个 section,这张表就得补一行。
 *
 * 漏一行的后果不是编译错,而是设置页把 `mission_worker` 原样当标题显示 ——
 * 正是 P1 要消灭的"裸 JSON key 标题"。所以直接从后端源码扫 `_defaults()`
 * 的键名,而不是在测试里手抄一份(手抄的清单会和实现一起漂移)。
 */
const SETTINGS_SERVICE = join(
  __dirname,
  "../../../services/local-api/app/core/settings_service.py",
);

/** 从 `_defaults()` 的 return 字典里取顶层键;`security` 由 `get_settings()` 注入。 */
function backendSectionKeys(): string[] {
  const src = readFileSync(SETTINGS_SERVICE, "utf8");
  const start = src.indexOf("    def _defaults() -> dict[str, Any]:");
  expect(start, "settings_service 里找不到 _defaults()").toBeGreaterThan(-1);
  const body = src.slice(start, src.indexOf("\n    def ", start + 10) === -1
    ? undefined
    : src.indexOf("\n    def ", start + 10));
  const keys = [...body.matchAll(/^ {12}"([a-z_]+)":/gm)].map((m) => m[1]);
  // get_settings() 在返回值上再挂一个 security 段(见 settings_service.py:290)
  expect(src).toContain('settings["security"] = self._security_state()');
  return [...keys, "security"];
}

describe("settings-groups:后端段 ↔ 分组名", () => {
  it("后端每一段要么有分组名、要么有专门面板(不留裸 key)", () => {
    const sections = backendSectionKeys();
    expect(sections.length, "扫到的段数异常,正则或源码结构变了").toBeGreaterThanOrEqual(15);
    const dedicated = new Set(DEDICATED_PANEL_GROUPS);
    const missing = sections.filter((key) => !(key in GROUP_LABELS) && !dedicated.has(key));
    expect(missing, `后端有而分组表/面板表都没有的段(会显示成裸 JSON key): ${missing.join(", ")}`)
      .toEqual([]);
  });

  it("有专门面板的段不进高级只读区(否则同一个值在页面上出现两次)", () => {
    const overlap = DEDICATED_PANEL_GROUPS.filter((key) => key in GROUP_LABELS);
    expect(overlap, `既登记了分组名又有专门面板: ${overlap.join(", ")}`).toEqual([]);
  });

  /**
   * 段级过滤会让**整段**从只读区消失 —— 对 `risk` 是错的:它一个段里混着
   * 8 个可编辑的 `tiered_*` 和 4 个没有编辑入口的字符串键。段级过滤留下的是
   * 重复行(可改 + 只读各一份),键级过滤留下的才是"每个值恰好一处"。
   */
  it("risk 段留在只读区,但 tiered_* 八个键被滤掉、四个字符串键保留", () => {
    expect(DEDICATED_PANEL_GROUPS, "risk 不该整段跳只读区").not.toContain("risk");

    const entries: [string, string][] = [
      ["max_position_pct", "10"],
      ["single_risk_pct", "2"],
      ["daily_loss_limit_pct", "5"],
      ["cooldown_seconds", "300"],
      ["tiered_hard_stop_pct", "0.02"],
      ["tiered_low_tier_threshold", "0.015"],
      ["tiered_first_tier_threshold", "0.03"],
      ["tiered_second_tier_threshold", "0.06"],
      ["tiered_low_tier_drop_pct", "0.5"],
      ["tiered_first_tier_drop_pct", "0.3"],
      ["tiered_second_tier_drop_pct", "0.2"],
      ["tiered_min_emit_interval_seconds", "30"],
    ];
    const kept = filterReadonlyEntries("risk", entries).map(([k]) => k);
    expect(kept).toEqual([
      "max_position_pct",
      "single_risk_pct",
      "daily_loss_limit_pct",
      "cooldown_seconds",
    ]);
  });

  it("DEDICATED_PANEL_KEYS 的每个键都由 TieredTrailingConfig 声明(滤掉的必须有编辑器)", () => {
    // 表里多写一个键 = 那个值在页面上彻底消失(既不在表单里也不在只读区)。
    // 所以拿真表单的默认值对象逐键核对,而不是在测试里手抄第二份清单。
    for (const [group, keys] of Object.entries(DEDICATED_PANEL_KEYS)) {
      expect(group in GROUP_LABELS, `${group} 不是已登记的只读段`).toBe(true);
      expect(keys.length, `${group} 的键表是空的(等于没过滤)`).toBeGreaterThan(0);
      for (const key of keys) {
        expect(
          Object.prototype.hasOwnProperty.call(TIERED_TRAILING_DEFAULTS, key),
          `${group} 滤掉了 ${key},但它不在任何表单里`,
        ).toBe(true);
      }
    }
  });

  it("未登记的段原样透传,不被误滤", () => {
    const entries: [string, string][] = [["a", "1"], ["b", "2"]];
    expect(filterReadonlyEntries("grid", entries)).toBe(entries);
  });

  it("分组表里没有后端不认识的多余段", () => {
    const known = new Set([...backendSectionKeys(), ...DEDICATED_PANEL_GROUPS]);
    const extra = Object.keys(GROUP_LABELS).filter((key) => !known.has(key));
    expect(extra, `分组表登记了后端不返回的段: ${extra.join(", ")}`).toEqual([]);
  });

  it("READONLY_GROUP_ORDER 与 GROUP_LABELS 的键集合一致(顺序表不许漏组)", () => {
    expect([...READONLY_GROUP_ORDER].sort()).toEqual(Object.keys(GROUP_LABELS).sort());
    expect(new Set(READONLY_GROUP_ORDER).size).toBe(READONLY_GROUP_ORDER.length);
    expect([...ADVANCED_GROUPS].sort()).toEqual(Object.keys(GROUP_LABELS).sort());
  });

  it("分组名不是裸 key(缩写如 DEX 例外)", () => {
    for (const [key, label] of Object.entries(GROUP_LABELS)) {
      expect(label, `${key} 的分组名疑似还是裸 key`).not.toBe(key);
      // DEX/AI 这类通用缩写中英同形,按"大写作缩写"放行
      const isAcronym = /^[A-Z][A-Z0-9 ]*$/.test(label);
      expect(
        isAcronym || /[一-鿿]/u.test(label),
        `${key} 的分组名既不是中文也不是缩写: ${label}`,
      ).toBe(true);
    }
  });
});

describe("settings-groups:兜底与排序", () => {
  it("未登记的 snake_case key 兜底成人读形式,不是原样输出", () => {
    expect(humanizeSettingKey("mission_worker")).toBe("Mission Worker");
    expect(humanizeSettingKey("max_rounds_per_mission")).toBe("Max Rounds Per Mission");
    expect(humanizeSettingKey("riskAlerts")).toBe("Risk Alerts");
    expect(settingsGroupLabel("brand_new_section")).toBe("Brand New Section");
  });

  it("已登记的 key 返回中文源串(不经过 humanize)", () => {
    expect(settingsGroupLabel("general")).toBe("通用");
    expect(settingsGroupLabel("security")).toBe("安全与密钥");
  });

  it("sortGroupKeys 按顺序表排;未知段排最后且保持后端原序", () => {
    expect(
      sortGroupKeys(["favorites", "general", "grid", "zzz_new", "aaa_new"]),
    ).toEqual(["general", "grid", "favorites", "zzz_new", "aaa_new"]);
  });

  it("sortGroupKeys 不修改入参", () => {
    const input = ["favorites", "general"];
    sortGroupKeys(input);
    expect(input).toEqual(["favorites", "general"]);
  });
});
