import type { ReactNode } from "react";
import "../../components/landing/landing-scale.css";

/**
 * (landing) 路由组 layout —— landing-scale.css 在这里注入,
 * 使 loading 骨架(先于 page 渲染)也吃到 16px rem 基准与 lp-container。
 */
export default function LandingLayout({ children }: { children: ReactNode }) {
  return children;
}
