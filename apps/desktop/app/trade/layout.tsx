import type { Scene } from "@/lib/page-manifest";
import { DashboardLayout } from "@/components/layout/dashboard-layout";

export default function TradeLayout({ children }: { children: React.ReactNode }) {
  return <DashboardLayout scene="trade">{children}</DashboardLayout>;
}
