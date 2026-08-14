import type {
  CoverageAvailability,
  DatasetCategory,
  RubinCoverageSummary,
  SurveyRegistryEntry as ExplorerSurvey,
} from "@/components/CoverageExplorer";
import type { SurveyRegistry } from "@/lib/survey-registry";

export type RubinFootprintFile = {
  schemaVersion: 1;
  release: string;
  generatedAt: string;
  counts: { tracts: number; patches: number };
  fields: ["tract", "center", "bounds", "patchCount", "patches"];
  tracts: Array<[
    number,
    [number, number],
    {
      ra: { start: number; end: number; width: number; wraps: boolean };
      dec_min: number;
      dec_max: number;
    },
    number,
    number[],
  ]>;
  validation: {
    completeAgainstLiveTable: boolean;
    catalogMinusPreliminaryTracts: number;
    note: string;
  };
};

export type ExternalOverlapFile = {
  schemaVersion: 1;
  generatedAt: string;
  index: { surveyId: "rubin-dp2"; release: string; tractCount: number };
  fields: [
    "tract",
    "confirmedSurveyIds",
    "approximateSurveyIds",
    "unresolvedSurveyIds",
    "pixelCachedSurveyIds",
  ];
  tracts: Array<[number, string[], string[], string[], string[]]>;
  surveySummaries: Array<{
    surveyId: string;
    family: string;
    coverageStatus: "resolved-moc" | "confirmed-all-sky" | "approximate" | "unresolved";
    confirmedTractCount: number;
    approximateTractCount: number;
    unresolvedTractCount: number;
    evidence: { method: string; sourceUrl: string; retrievedAt: string; note: string };
  }>;
};

export type SelectedRegionsFile = {
  schemaVersion: 1;
  generatedAt: string;
  requestedCount: number;
  selectedCount: number;
  regions: Array<{
    id: string;
    tract: number;
    center: [number, number];
    radiusArcmin: number;
    confirmedSurveyIds: string[];
    surveyFamilies: string[];
    pixelCachedSurveyIds: string[];
    selectionReasons: string[];
  }>;
};

export type CacheManifestFile = {
  schemaVersion: "layers-public-cache-manifest-v1";
  honestStatus: string;
  summary: {
    plannedRegionCount: number;
    plannedJobCount: number;
    metadataResponseCount: number;
    cachedScienceInputCandidateCount: number;
    validatedScienceInputCount: number;
    comparisonReadyCount: number;
  };
};

export type ResolvedProductFootprint = {
  surveyId: string;
  status: string;
  confirmedRubinTractCount: number;
  confirmedRubinTractIds: number[];
  productName?: string;
  release?: string;
  coverageSemantics: string;
  eligibleAsFullRegistryFootprint: boolean;
};

const familyCategory: Record<string, DatasetCategory> = {
  "optical-baseline": "optical",
  "uv-ir": "uv-ir",
  astrometry: "astrometry",
  "time-domain": "time-domain",
  spectroscopy: "spectroscopy",
  "high-energy": "x-ray",
  radio: "radio",
  "neutral-gas": "gas",
  "high-resolution": "high-resolution",
  lensing: "lensing",
  "cmb-large-scale-structure": "microwave",
};

// These are the three tracts with validated, common-grid Rubin + LoTSS
// display products. Do not add a tract here until its pixels pass alignment QA.
const pilotFieldByTract: Record<number, string> = {
  10689: "ugc00634",
  11162: "ugc00191",
  11411: "ugc00891",
};

function coverageEvidence(method: string): ExplorerSurvey["coverageEvidence"] {
  if (method.toLowerCase().includes("moc") && method.toLowerCase().includes("archive")) {
    return "moc-and-archive-query";
  }
  if (method.toLowerCase().includes("moc")) return "moc";
  return "archive-query";
}

export function buildCoverageExplorerData(
  footprint: RubinFootprintFile,
  overlaps: ExternalOverlapFile,
  selectedRegions: SelectedRegionsFile,
  registry: SurveyRegistry,
  cacheManifest?: CacheManifestFile,
  workspaceIndex?: {
    imageReadyTractIds?: number[];
    alignedViewerTractIds?: number[];
    pixelReadySurveyIdsByTract?: Record<number, string[]>;
    previewByTract?: Record<number, string>;
    layerThumbnailsByTract?: Record<number, { surveyName: string; band: string; image: string }[]>;
    scienceInputCandidateCount?: number;
    validatedScienceInputCount?: number;
    resolvedProductFootprints?: ResolvedProductFootprint[];
  },
): { coverage: RubinCoverageSummary; surveys: ExplorerSurvey[] } {
  const overlapByTract = new Map(overlaps.tracts.map((row) => [row[0], row]));
  const resolvedProducts = workspaceIndex?.resolvedProductFootprints ?? [];
  const fullResolvedProducts = resolvedProducts.filter((product) => product.eligibleAsFullRegistryFootprint);
  const subsetResolvedProducts = resolvedProducts.filter((product) => !product.eligibleAsFullRegistryFootprint);
  const fullResolvedBySurvey = new Map(fullResolvedProducts.map((product) => [product.surveyId, product]));
  const subsetResolvedBySurvey = new Map(subsetResolvedProducts.map((product) => [product.surveyId, product]));
  const fullResolvedSurveyIdsByTract = new Map<number, string[]>();
  const subsetResolvedSurveyIdsByTract = new Map<number, string[]>();
  for (const product of fullResolvedProducts) {
    for (const tract of product.confirmedRubinTractIds) {
      const surveyIds = fullResolvedSurveyIdsByTract.get(tract) ?? [];
      surveyIds.push(product.surveyId);
      fullResolvedSurveyIdsByTract.set(tract, surveyIds);
    }
  }
  for (const product of subsetResolvedProducts) {
    for (const tract of product.confirmedRubinTractIds) {
      const surveyIds = subsetResolvedSurveyIdsByTract.get(tract) ?? [];
      surveyIds.push(product.surveyId);
      subsetResolvedSurveyIdsByTract.set(tract, surveyIds);
    }
  }
  const selectedCounts = new Map<number, number>();
  for (const region of selectedRegions.regions) {
    selectedCounts.set(region.tract, (selectedCounts.get(region.tract) ?? 0) + 1);
  }

  const coverage: RubinCoverageSummary = {
    release: footprint.release,
    totalTractCount: footprint.counts.tracts,
    indexedTractCount: footprint.validation.completeAgainstLiveTable ? footprint.counts.tracts : footprint.tracts.length,
    coverageUpdatedAt: overlaps.generatedAt || footprint.generatedAt,
    selectedRegionCount: selectedRegions.selectedCount,
    selectedRegionGoal: selectedRegions.requestedCount,
    acquisition: cacheManifest ? {
      plannedJobCount: cacheManifest.summary.plannedJobCount,
      metadataResponseCount: cacheManifest.summary.metadataResponseCount,
      cachedScienceInputCandidateCount: workspaceIndex?.scienceInputCandidateCount ?? cacheManifest.summary.cachedScienceInputCandidateCount,
      validatedScienceInputCount: workspaceIndex?.validatedScienceInputCount ?? cacheManifest.summary.validatedScienceInputCount,
      comparisonReadyCount: cacheManifest.summary.comparisonReadyCount,
      honestStatus: workspaceIndex?.validatedScienceInputCount !== undefined
        ? `${workspaceIndex.validatedScienceInputCount} validated Rubin or historical science inputs are cached; aligned displays remain separate from quantitative comparison readiness.`
        : cacheManifest.honestStatus,
    } : undefined,
    tracts: footprint.tracts.map(([tract, [raDeg, decDeg]]) => {
      const overlap = overlapByTract.get(tract);
      const pilotField = pilotFieldByTract[tract];
      const imageReady = workspaceIndex?.imageReadyTractIds?.includes(tract) ?? false;
      const alignedViewer = workspaceIndex?.alignedViewerTractIds?.includes(tract) ?? Boolean(pilotField);
      const cachedSurveyIds = new Set(overlap?.[4] ?? []);
      const confirmedSurveyIds = new Set(overlap?.[1] ?? []);
      for (const surveyId of fullResolvedSurveyIdsByTract.get(tract) ?? []) confirmedSurveyIds.add(surveyId);
      if (imageReady) cachedSurveyIds.add("legacy-surveys-dr10");
      for (const surveyId of workspaceIndex?.pixelReadySurveyIdsByTract?.[tract] ?? []) cachedSurveyIds.add(surveyId);
      return {
        id: String(tract),
        raDeg,
        decDeg,
        overlapSurveyIds: [...confirmedSurveyIds],
        subsetSurveyIds: subsetResolvedSurveyIdsByTract.get(tract) ?? [],
        pixelCachedSurveyIds: [...cachedSurveyIds],
        selectedRegionCount: selectedCounts.get(tract) ?? 0,
        label: `Rubin tract ${tract}`,
        href: `/tract/${tract}`,
        previewImage: workspaceIndex?.previewByTract?.[tract],
        layerThumbnails: workspaceIndex?.layerThumbnailsByTract?.[tract] ?? [],
        viewerStatus: alignedViewer ? "aligned-viewer" as const : imageReady ? "image-only" as const : "evidence-only" as const,
      };
    }),
  };

  const summaryBySurvey = new Map(overlaps.surveySummaries.map((summary) => [summary.surveyId, summary]));
  const surveys: ExplorerSurvey[] = registry.surveys.map((survey) => {
    const summary = summaryBySurvey.get(survey.id);
    const resolvedProduct = fullResolvedBySurvey.get(survey.id);
    const subsetProduct = subsetResolvedBySurvey.get(survey.id);
    const fullOverlapCount = resolvedProduct?.confirmedRubinTractCount ?? summary?.confirmedTractCount ?? 0;
    const subsetOverlapCount = subsetProduct?.confirmedRubinTractCount ?? 0;
    const pixelCachedTractCount = coverage.tracts.filter((tract) => tract.pixelCachedSurveyIds?.includes(survey.id)).length;
    let availability: CoverageAvailability = "metadata-only";
    if (pixelCachedTractCount > 0) availability = "pixels-ready";
    else if (survey.accessStatus === "planned") availability = "planned";
    else if (!resolvedProduct && !subsetProduct && (!summary || summary.coverageStatus === "unresolved")) availability = "unavailable";
    else if (fullOverlapCount === 0 && subsetOverlapCount === 0) availability = "no-overlap";

    return {
      id: survey.id,
      name: survey.name,
      shortName: survey.shortName,
      category: familyCategory[survey.family] ?? survey.family,
      availability,
      release: survey.release,
      overlappingTractCount: fullOverlapCount,
      subsetTractCount: subsetProduct?.confirmedRubinTractCount,
      subsetLabel: subsetProduct?.productName,
      pixelCachedTractCount,
      coverageEvidence: resolvedProduct ? "moc" : summary ? coverageEvidence(summary.evidence.method) : undefined,
      coverageUpdatedAt: summary?.evidence.retrievedAt,
      description: survey.scienceRoles[0],
      sourceUrl: survey.provenanceUrls[0],
    };
  });

  return { coverage, surveys };
}
