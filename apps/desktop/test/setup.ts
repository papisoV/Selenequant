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

// toast 默认走 window.dispatchEvent(CustomEvent)，jsdom 支持但需兜底
afterEach(() => {
  vi.clearAllMocks();
});
