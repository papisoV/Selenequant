import "@testing-library/jest-dom/vitest";
import { vi, afterEach } from "vitest";

// jsdom 不实现 matchMedia，组件库可能调用
if (typeof window !== "undefined" && !window.matchMedia) {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }),
  });
}

// jsdom 不实现 IntersectionObserver（equity-curve-chart/landing 用到）
if (typeof window !== "undefined" && !window.IntersectionObserver) {
  class IntersectionObserverStub {
    root = null;
    rootMargin = "";
    thresholds = [] as number[];
    observe = vi.fn();
    unobserve = vi.fn();
    disconnect = vi.fn();
    takeRecords = vi.fn(() => []);
  }
  Object.defineProperty(window, "IntersectionObserver", {
    writable: true,
    value: IntersectionObserverStub,
  });
}

// jsdom 不实现 ResizeObserver（equity-curve-chart 的 lightweight-charts 用到）
if (typeof window !== "undefined" && !window.ResizeObserver) {
  class ResizeObserverStub {
    observe = vi.fn();
    unobserve = vi.fn();
    disconnect = vi.fn();
  }
  Object.defineProperty(window, "ResizeObserver", {
    writable: true,
    value: ResizeObserverStub,
  });
}

// jsdom 不实现 canvas 2D context（lightweight-charts 的 kline-chart / equity-curve-chart 用到）。
// 缺这个桩时 LWC 的 rAF 渲染循环会抛 "HTMLCanvasElement's getContext() method: without
// installing the canvas npm package" + PriceAxisWidget._internal_optimalWidth 里的
// "Error: Value is null"，vitest 报 Unhandled Errors（会让测试假绿），所以必须在挂载前补上。
if (typeof window !== "undefined" && typeof HTMLCanvasElement !== "undefined") {
  const noop = () => {};
  const makeCtx = () => {
    const ctx: Record<string, unknown> = {
      canvas: null,
      // 量测类必须返回真结构，返回 undefined 会让 LWC 内部 .width 取值崩
      measureText: (t: string) => ({ width: String(t ?? "").length * 6 }),
      getImageData: () => ({ data: new Uint8ClampedArray(4), width: 0, height: 0 }),
      createImageData: () => ({ data: new Uint8ClampedArray(4), width: 0, height: 0 }),
      createLinearGradient: () => ({ addColorStop: noop }),
      createRadialGradient: () => ({ addColorStop: noop }),
      createPattern: () => null,
      getLineDash: () => [],
      isPointInPath: () => false,
      isPointInStroke: () => false,
      getTransform: () => ({ a: 1, b: 0, c: 0, d: 1, e: 0, f: 0 }),
      getContextAttributes: () => ({ alpha: true }),
    };
    // 其余绘制方法（fillRect/arc/setTransform…）一律 noop，避免逐个枚举漏项
    return new Proxy(ctx, {
      get: (t, k) => (k in t ? t[k as string] : noop),
      set: (t, k, v) => {
        (t as Record<string, unknown>)[k as string] = v;
        return true;
      },
    });
  };
  HTMLCanvasElement.prototype.getContext = function (this: HTMLCanvasElement) {
    return makeCtx();
  } as unknown as HTMLCanvasElement["getContext"];
}

// toast 默认走 window.dispatchEvent(CustomEvent)，jsdom 支持但需兜底
afterEach(() => {
  vi.clearAllMocks();
});
