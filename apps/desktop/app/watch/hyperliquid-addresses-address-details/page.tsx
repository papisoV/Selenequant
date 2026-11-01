import { PageRenderer } from "@/components/shared/page-renderer";
import { ALL_PAGES } from "@/lib/page-manifest";

export default function Page() {
  const meta = ALL_PAGES.find((p) => p.slug === "hyperliquid-addresses-address-details")!;
  return <PageRenderer meta={meta} />;
}
