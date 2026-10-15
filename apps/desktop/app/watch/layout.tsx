import type { Scene } from "@/lib/page-manifest";
import { DashboardLayout } from "@/components/layout/dashboard-layout";

export default function WatchLayout({ children }: { children: React.ReactNode }) {
  return <DashboardLayout scene="watch">{children}</DashboardLayout>;
}
