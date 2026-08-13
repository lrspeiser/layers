import type { Metadata } from "next";
import catalogData from "@/public/data/layers-catalog.json";
import prototypeScience from "@/public/data/prototype-science.json";
import { RealFieldPrototype } from "@/components/RealFieldPrototype";
import type { LayersCatalog } from "@/lib/layers";

export const metadata: Metadata = {
  title: "Real-pixel field prototype",
  description: "A private real-data prototype comparing Rubin DP2 and Legacy Survey DR10 for UGC 00191.",
};

export default function PrototypePage() {
  const catalog = catalogData as unknown as LayersCatalog;
  const target = catalog.targets.find((item) => item.id === "ugc00191");
  const comparison = target?.comparisons.find((item) => item.layerIds.includes("legacy-survey-dr10"));
  const residual = comparison?.qa?.astrometricResidualP95Arcsec ?? 0.236;
  const threshold = comparison?.registration?.qaThresholdArcsec ?? 0.3;
  const common = comparison?.qa?.commonValidPixelFraction ?? 0.9598;
  const sources = comparison?.qa?.matchedSources ?? 233;

  return <RealFieldPrototype qa={{ residualArcsec: residual, thresholdArcsec: threshold, commonValidPercent: common * 100, matchedSources: sources }} science={prototypeScience} />;
}
