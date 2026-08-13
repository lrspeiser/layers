export type LayerKind = "image" | "map" | "catalog" | "profile" | "spectrum" | "simulation";
export type RenderMode = "image" | "overlay" | "plot" | "table" | "metadata";
export type LayerAvailability =
  | "available"
  | "available-local"
  | "published"
  | "metadata-match"
  | "no-valid-pixels"
  | "not-covered";

export type Layer = {
  id: string;
  survey: string;
  release: string;
  instrument: string;
  kind: LayerKind;
  availability: LayerAvailability;
  renderMode: RenderMode;
  bands: string[];
  bandCoverage?: Record<string, number>;
  datasetCount?: number;
  datasetIds?: string[];
  units: Record<string, string>;
  calibration: string;
  hasVariance: boolean;
  hasMask: boolean;
  hasWcs: boolean;
  note: string;
  provenance: Record<string, string>;
  assets?: {
    preview?: string;
    bands?: Record<string, string>;
    data?: string;
  };
  profileSummary?: {
    acceptedPhotometryPoints: number;
    maximumAcceptedRadiusArcsec: number;
    rotationCurvePoints: number;
    maximumRotationRadiusKpc: number;
  };
  catalogSummary?: {
    logStellarMassMsun: number;
    uncertaintyDex: number;
    gMinusW1Mag: number;
    massToLight: number;
  };
};

export type SparcProfilePoint = {
  radiusArcsec: number;
  surfaceBrightnessMagArcsec2: number;
  accepted: boolean;
  uncertaintyMag: number | null;
};

export type SparcRotationPoint = {
  radiusKpc: number;
  observedVelocityKmS: number;
  velocityUncertaintyKmS: number;
  gasVelocityKmS: number;
  diskVelocityKmS: number;
  bulgeVelocityKmS: number;
  diskSurfaceBrightnessLsunPc2: number;
  bulgeSurfaceBrightnessLsunPc2: number;
};

export type SparcProfile = {
  targetId: string;
  sparcId: string;
  distanceMpc: number | null;
  surfaceBrightness: SparcProfilePoint[];
  rotationCurve: SparcRotationPoint[];
  summary: NonNullable<Layer["profileSummary"]>;
  provenance: Record<string, string>;
};

export type Registration = {
  layerIds: [string, string];
  commonWcs: boolean;
  commonFootprint: boolean;
  psfMatched: boolean;
  skyMatched: boolean;
  unitsMatched: boolean;
  filterMatched: boolean;
  filterTransform?: string;
  maxResidualArcsec?: number;
  qaThresholdArcsec?: number;
  limitations: string[];
};

export type DifferenceMeasurement = {
  id: string;
  label: string;
  quantity: string;
  value: number;
  unit: string;
  statisticalUncertainty: number;
  systematicUncertainty: number;
  expectedRange: [number, number];
  expectedCenter?: number;
  significanceSigma: number;
  classification: "expected" | "noteworthy" | "large";
  provenance: string[];
  caveats: string[];
};

export type Inference = {
  id: string;
  domain: "baryonic-mass" | "morphology" | "lensing" | "distance" | "source-counts" | "other";
  observation: string;
  modelDependentInterpretation: string;
  confidence: "unreviewed" | "candidate" | "supported" | "confirmed";
  assumptions: string[];
};

export type AssumptionAudit = {
  id: string;
  rank: number;
  title: string;
  priorAssumption: string;
  newEvidence: string;
  affectedInference: string;
  confidence: "unreviewed" | "candidate" | "supported" | "confirmed";
  priorityScore: number;
  evidenceMagnitude: {
    metric: string;
    value: number;
    unit: string;
    passThreshold: number;
    thresholdMultiple: number;
    qualifiedCells: number;
    cellsWithinTrainingSupport: number;
  };
  systematicAlternatives: string[];
  recommendedFollowUp: string[];
  provenance: string[];
  caveat: string;
  independentCheck?: {
    survey: string;
    status: string;
    gate: string;
    registrationPass: boolean;
    registrationP95Arcsec: number | null;
    passThresholdArcsec: number | null;
    qualifiedForArbitration: boolean;
    note: string;
    provenance: string[];
  };
};

export type Comparison = {
  id: string;
  comparisonKey?: string;
  comparisonMode?: "image" | "catalog-profile";
  layerIds: [string, string];
  status: "blocked" | "qa" | "published";
  registration?: Registration;
  compatibility?: {
    targetIdentityMatched: boolean;
    quantityMatched: boolean;
    unitsMatched: boolean;
    distanceScaleShared: boolean;
    modelDeclared: boolean;
    limitations: string[];
  };
  catalogValues?: {
    wiseLogStellarMassMsun: number;
    wiseStatisticalUncertaintyDex: number;
    wiseMassToLight: number;
    wiseGMinusW1Mag: number;
    sparcLuminosity36BillionLsun: number;
    sparcLuminosity36UncertaintyBillionLsun: number;
    sparcFixedMassToLight: number;
    sparcBaselineLogStellarMassMsun: number;
  };
  qa?: {
    band?: string;
    comparisonLayerLabel?: string;
    commonValidPixelFraction?: number;
    matchedSources?: number;
    astrometricResidualP95Arcsec?: number;
    astrometryPass?: boolean;
    rubinMedianFwhmArcsec?: number;
    comparisonMedianFwhmArcsec?: number;
    reconciliationStatus?: string;
    matchedCommonValidPixelFraction?: number;
    postMatchAstrometricResidualP95Arcsec?: number;
    postMatchFractionalFwhmDifference?: number;
    filterMatchBlocking?: boolean;
    pointSourceCalibrationPass?: boolean;
    filterHeldOutRmsMag?: number;
    extendedSourceTransferPass?: boolean;
    extendedSourceTransferStatus?: string;
    extendedSourceResolvedCells?: number;
    extendedSourceMedianAbsoluteResidualMag?: number;
    extendedSourceRobustScatterMag?: number;
    injectionRecoveryStatus?: string;
    injectionNullTestPass?: boolean;
    injectionRecoveryGridPass?: boolean;
    injectionProfile?: string;
    injectionEffectiveRadiiArcsec?: number[];
  };
  products?: {
    matchedPairSha256?: string;
    sourceRubinSha256?: string;
    sourceComparisonSha256?: string;
    qaPackage?: string;
  };
  measurements: DifferenceMeasurement[];
  inferences: Inference[];
  assumptionAudits: AssumptionAudit[];
};

export type PilotAudit = {
  id: string;
  outcome: "no-valid-pixels" | "registration-blocked" | "filter-transfer-blocked";
  stage: "pixel-coverage" | "registration" | "filter-response";
  observation: string;
  metric: { label: string; value: number; unit: string; passThreshold: number; comparison: string };
  claimStatus: "blocked";
  evidence: Array<{ path: string; sha256: string }>;
  nextAction: string;
};

export type LayerTarget = {
  id: string;
  name: string;
  identifiers: Record<string, string>;
  center: { raDeg: number; decDeg: number; frame: "ICRS" };
  region: { shape: "square" | "circle" | "polygon"; widthArcmin: number };
  selection: { sample: string; bibcode?: string; majorAxisArcmin?: number };
  layers: Layer[];
  comparisons: Comparison[];
  pilotAudit?: PilotAudit;
};

export type LayersCatalog = {
  schemaVersion: 1;
  product: "Layers";
  release: string;
  generatedAt: string;
  targetSelection: { name: string; count: number; complete: boolean };
  summary: {
    targets: number;
    rubinSiaMatches: number;
    rubinUsableLocal: number;
    rubinFootprintFalsePositives: number;
    legacySurveyUsableLocal?: number;
    panStarrsUsableLocal?: number;
    externalImageLayers?: number;
    externalCatalogLayers?: number;
    allWisePublished?: number;
    localImageLayers?: number;
    registrationAudits?: number;
    pilotAudits?: number;
    assumptionsWorthRechecking?: number;
    publishedComparisons: number;
  };
  targets: LayerTarget[];
};

export function layerStatusLabel(layer: Layer): string {
  const labels: Record<LayerAvailability, string> = {
    available: "Available",
    "available-local": "Local science pixels",
    published: "Published",
    "metadata-match": "Footprint match",
    "no-valid-pixels": "No valid pixels",
    "not-covered": "No coverage",
  };
  return labels[layer.availability];
}

export function comparisonIsSwipeable(comparison: Comparison, layers: Layer[]): boolean {
  if (comparison.status !== "published" || !comparison.registration) return false;
  const selected = comparison.layerIds.map((id) => layers.find((layer) => layer.id === id));
  return selected.every((layer) => layer?.kind === "image" && Boolean(layer.assets?.preview))
    && comparison.registration.commonWcs
    && comparison.registration.commonFootprint
    && comparison.registration.psfMatched
    && comparison.registration.skyMatched
    && comparison.registration.unitsMatched
    && comparison.registration.filterMatched;
}
