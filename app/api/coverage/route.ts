import { NextResponse } from "next/server";
import footprintData from "@/public/data/coverage/rubin-dp2-footprint.json";
import overlapData from "@/public/data/coverage/external-overlaps.json";
import selectedRegionsData from "@/public/data/coverage/selected-regions.json";
import cacheManifestData from "@/public/data/coverage/cache-manifest.json";
import surveyRegistryData from "@/public/data/survey-registry.json";
import acquisition50Data from "@/public/data/coverage/acquisition-50-summary.json";
import rubinPixels50Data from "@/public/data/coverage/rubin-pixels-50.json";
import conservativeSubsetData from "@/public/data/coverage/conservative-subset-evidence.json";
import legacyNormalizedData from "@/public/data/layers/selected-regions/legacy-dr10.json";
import selectedComparisonsData from "@/public/data/layers/selected-regions/rubin-reference-comparisons.json";
import familyTractManifestData from "@/public/data/layers/family-examples/tract-manifest.json";
import panstarrsGapFillData from "@/public/data/layers/panstarrs-gap-fill/manifest.json";
import largeFootprintResolutionData from "@/public/data/coverage/large-footprint-resolution.json";
import hscKidsGapAuditData from "@/public/data/coverage/hsc-kids-gap-audit.json";
import hscPublicProductsData from "@/public/data/coverage/hsc-public-products.json";
import desiDr1ResolutionData from "@/public/data/coverage/desi-dr1-resolution.json";
import goalAuditData from "@/public/data/coverage/goal-audit.json";
import tractProductIndexData from "@/public/data/layers/tract-product-index.json";

export function GET() {
  return NextResponse.json({
    schemaVersion: 1,
    product: "Layers coverage index",
    footprint: footprintData,
    overlaps: overlapData,
    selectedRegions: selectedRegionsData,
    cacheManifest: cacheManifestData,
    acquisition50: acquisition50Data,
    pixelProducts: {
      rubin: rubinPixels50Data,
      historical: legacyNormalizedData,
      panstarrsGapFill: panstarrsGapFillData,
      alignedDisplays: selectedComparisonsData,
      tractProductIndex: tractProductIndexData,
    },
    conservativeSubsetEvidence: conservativeSubsetData,
    resolvedProductFootprints: largeFootprintResolutionData,
    hscKidsGapAudit: hscKidsGapAuditData,
    hscPublicProducts: hscPublicProductsData,
    desiDr1Resolution: desiDr1ResolutionData,
    objectiveAudit: goalAuditData,
    tractEvidenceRoutes: familyTractManifestData,
    surveyRegistry: surveyRegistryData,
  }, {
    headers: { "Cache-Control": "public, max-age=300, stale-while-revalidate=3600" },
  });
}
