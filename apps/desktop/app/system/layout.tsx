import type { Scene } from "@/lib/page-manifest";
import { DashboardLayout } from "@/components/layout/dashboard-layout";

export default function SystemLayout({ children }: { children: React.ReactNode }) {
  return <DashboardLayout scene="system">{children}</DashboardLayout>;
}
