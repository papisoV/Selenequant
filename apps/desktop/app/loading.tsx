import { MockPreviewLoading } from "@/components/loading/mock-preview-loading";
import { CategoryLoadingBanner } from "@/components/loading/category-loading-banner";

export default function Loading() {
  return (
    <>
      <CategoryLoadingBanner />
      <MockPreviewLoading />
    </>
  );
}
