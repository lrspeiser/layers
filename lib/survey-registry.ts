import rawSurveyRegistry from "@/public/data/survey-registry.json";

export type LayerPrimitive =
  | "raster"
  | "catalog"
  | "vector"
  | "spectrum"
  | "time-series"
  | "cube";

export type WavelengthDomain =
  | "gamma-ray"
  | "x-ray"
  | "ultraviolet"
  | "optical"
  | "near-infrared"
  | "mid-infrared"
  | "far-infrared"
  | "microwave"
  | "millimeter"
  | "radio"
  | "non-photonic";

export type CoverageType =
  | "all-sky"
  | "wide-area"
  | "regional"
  | "pointed"
  | "object-targeted";

export type AccessStatus = "public" | "registration" | "mixed" | "proprietary" | "planned";

export type AccessProtocol =
  | "sia2"
  | "soda"
  | "tap"
  | "datalink"
  | "hips"
  | "cone-search"
  | "mast-api"
  | "irsa-api"
  | "casda"
  | "archive-api"
  | "http"
  | "bulk-download";

export type UiRenderMode =
  | "swipe"
  | "rgb"
  | "single-band"
  | "difference"
  | "contours"
  | "markers"
  | "vectors"
  | "spectrum"
  | "light-curve"
  | "channel-map"
  | "moment-map"
  | "coverage";

export type DifferenceSemanticId =
  | "rubin-only-detection"
  | "reference-only-detection"
  | "flux-residual"
  | "color-excess"
  | "morphology-change"
  | "astrometric-motion"
  | "temporal-change"
  | "counterpart-association"
  | "gas-star-offset"
  | "mass-light-offset"
  | "redshift-consistency"
  | "foreground-rejection";

export type SurveyEndpoint = {
  label: string;
  protocol: AccessProtocol;
  url: string;
  authentication: "none" | "account" | "token" | "varies";
  purpose: string;
};

export type CoverageDescriptor = {
  type: CoverageType;
  approximateAreaSqDeg: number | null;
  geometrySource: "moc" | "hips" | "sia-query" | "observation-footprints" | "static-footprint" | "catalog-derived";
  footprintEndpoint: string | null;
  mocId: string | null;
  coverageEndpoint: string | null;
  machineReadableStatus: "exact" | "queryable" | "fallback-static" | "catalog-derived";
  fallback: string;
  notes: string;
};

export type CachePolicy = {
  strategy: "metadata-local" | "cutout-on-demand" | "pin-cache" | "derived-products-only";
  downloadWholeArchive: false;
  cacheProducts: string[];
  revalidateDays: number;
  maximumCutoutArcmin: number | null;
};

export type DifferenceSemantic = {
  id: DifferenceSemanticId;
  label: string;
  rubinQuestion: string;
  requiredControls: string[];
};

export type SurveyRegistryEntry = {
  id: string;
  name: string;
  shortName: string;
  organization: string;
  release: string;
  family: "optical-baseline" | "uv-ir" | "astrometry" | "time-domain" | "spectroscopy" | "high-energy" | "radio" | "neutral-gas" | "high-resolution" | "lensing" | "cmb-large-scale-structure";
  priority: 1 | 2 | 3;
  accessStatus: AccessStatus;
  layerPrimitives: LayerPrimitive[];
  wavelengthDomains: WavelengthDomain[];
  bandsOrProducts: string[];
  scienceRoles: string[];
  rubinAdds: string[];
  coverage: CoverageDescriptor;
  endpoints: SurveyEndpoint[];
  cachePolicy: CachePolicy;
  uiRenderModes: UiRenderMode[];
  differenceSemantics: DifferenceSemantic[];
  provenanceUrls: string[];
  caveats: string[];
};

export type SurveyRegistry = {
  schemaVersion: 1;
  product: "Layers";
  generatedAt: string;
  indexSurvey: {
    id: "rubin-dp2";
    name: string;
    release: string;
    role: "spatial-index";
    coverageUnit: "tract";
    provenanceUrls: string[];
  };
  policies: {
    discovery: string;
    storage: string;
    comparison: string;
    colorSemantics: Record<"rubinOnly" | "referenceOnly" | "consistent" | "invalid", string>;
  };
  surveys: SurveyRegistryEntry[];
};

const primitiveValues = new Set<LayerPrimitive>(["raster", "catalog", "vector", "spectrum", "time-series", "cube"]);
const differenceValues = new Set<DifferenceSemanticId>([
  "rubin-only-detection",
  "reference-only-detection",
  "flux-residual",
  "color-excess",
  "morphology-change",
  "astrometric-motion",
  "temporal-change",
  "counterpart-association",
  "gas-star-offset",
  "mass-light-offset",
  "redshift-consistency",
  "foreground-rejection",
]);

export function validateSurveyRegistry(value: unknown): asserts value is SurveyRegistry {
  if (!value || typeof value !== "object") throw new Error("Survey registry must be an object");
  const registry = value as Partial<SurveyRegistry>;
  if (registry.schemaVersion !== 1 || registry.product !== "Layers") {
    throw new Error("Unsupported survey registry schema");
  }
  if (!registry.indexSurvey || registry.indexSurvey.id !== "rubin-dp2") {
    throw new Error("Rubin DP2 must remain the spatial index");
  }
  if (!Array.isArray(registry.surveys) || registry.surveys.length === 0) {
    throw new Error("Survey registry is empty");
  }
  const ids = new Set<string>();
  for (const survey of registry.surveys) {
    if (!survey.id || ids.has(survey.id)) throw new Error(`Duplicate or missing survey id: ${survey.id}`);
    ids.add(survey.id);
    if (!survey.layerPrimitives.length || survey.layerPrimitives.some((item) => !primitiveValues.has(item))) {
      throw new Error(`Invalid layer primitive for ${survey.id}`);
    }
    if (!survey.provenanceUrls.length || survey.provenanceUrls.some((url) => !url.startsWith("https://"))) {
      throw new Error(`Missing HTTPS provenance for ${survey.id}`);
    }
    if (survey.cachePolicy.downloadWholeArchive !== false) {
      throw new Error(`Whole-archive download is forbidden for ${survey.id}`);
    }
    if (survey.differenceSemantics.some((semantic) => !differenceValues.has(semantic.id))) {
      throw new Error(`Invalid difference semantic for ${survey.id}`);
    }
  }
}

validateSurveyRegistry(rawSurveyRegistry);

export const surveyRegistry: SurveyRegistry = rawSurveyRegistry;
export const surveysById = new Map(surveyRegistry.surveys.map((survey) => [survey.id, survey]));

export function surveysForPrimitive(primitive: LayerPrimitive): SurveyRegistryEntry[] {
  return surveyRegistry.surveys.filter((survey) => survey.layerPrimitives.includes(primitive));
}

export function surveysForDifference(semantic: DifferenceSemanticId): SurveyRegistryEntry[] {
  return surveyRegistry.surveys.filter((survey) =>
    survey.differenceSemantics.some((candidate) => candidate.id === semantic),
  );
}

export function publicSurveyEndpoints(surveyId: string): SurveyEndpoint[] {
  const survey = surveysById.get(surveyId);
  if (!survey) return [];
  return survey.endpoints.filter((endpoint) => endpoint.authentication === "none");
}
