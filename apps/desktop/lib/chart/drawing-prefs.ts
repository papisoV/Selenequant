// 绘图工具的**全局偏好** —— 连续绘制与磁吸。
//
// 单独成文件而不是塞进 fib-object / drawings store:
//   - 它们不是某个绘图对象的数据,改它们不该触发"整棵树重新序列化落盘";
//   - 它们是**用户的使用习惯**(像"单位用公制还是英制"),换一张图、重开应用
//     都该保持;而 `activeTool` 是临时的交互态,重开必须回到"选择"。
// 所以 `activeTool` 不落盘、这里落盘,两者的生命周期刻意相反。
//
// 落盘走的 key 也刻意与 `selenespace-fib-drawings` 分开:绘图对象的 schema
// 版本管的是"线上数据长什么样",这里只是两个开关,混进去会逼着每次调参
// 都去动 DRAWINGS_SCHEMA_VERSION。

/** 磁吸档位。语义照 TradingView:off / 弱(靠近才吸) / 强(总是吸)。 */
export type MagnetMode = "off" | "weak" | "strong";

export interface DrawingPrefs {
  /**
   * 连续绘制(TradingView 的 Keep drawing)。开 = 画完一个对象后工具仍武装,
   * 可以接着画下一个;关 = 每画完一个自动回到选择态。
   *
   * **默认开**:这是本仓库此前的既成行为(实测连画 3 条趋势线工具都不退出),
   * 写成默认关会凭空改变老用户的手感。
   */
  keepDrawing: boolean;
  /** 磁吸。默认关 —— 与 TradingView 同,免得用户"明明点在这里却吸到了别处"。 */
  magnet: MagnetMode;
}

export const DEFAULT_DRAWING_PREFS: DrawingPrefs = {
  keepDrawing: true,
  magnet: "off",
};

/** 单独的 localStorage key。与绘图对象存档分开,见文件头注释。 */
export const DRAWING_PREFS_KEY = "selenespace-drawing-prefs";

const MAGNET_MODES: readonly MagnetMode[] = ["off", "weak", "strong"];

function parseMagnet(raw: unknown): MagnetMode {
  return MAGNET_MODES.find((m) => m === raw) ?? DEFAULT_DRAWING_PREFS.magnet;
}

/**
 * 解析偏好。**坏一个字段只回退那一个**,不整包回默认 —— 用户只把磁吸改坏了,
 * 不该连带把连续绘制也重置掉。
 */
export function parseDrawingPrefs(raw: unknown): DrawingPrefs {
  if (!raw || typeof raw !== "object") return { ...DEFAULT_DRAWING_PREFS };
  const o = raw as Record<string, unknown>;
  return {
    keepDrawing: typeof o.keepDrawing === "boolean" ? o.keepDrawing : DEFAULT_DRAWING_PREFS.keepDrawing,
    magnet: parseMagnet(o.magnet),
  };
}

export function serializeDrawingPrefs(prefs: DrawingPrefs): string {
  return JSON.stringify({ keepDrawing: prefs.keepDrawing, magnet: prefs.magnet });
}

/** 从 JSON 字符串解析;任何异常都收敛为默认值,不抛。 */
export function parseDrawingPrefsJson(text: string | null): DrawingPrefs {
  if (!text) return { ...DEFAULT_DRAWING_PREFS };
  try {
    return parseDrawingPrefs(JSON.parse(text));
  } catch {
    return { ...DEFAULT_DRAWING_PREFS };
  }
}
