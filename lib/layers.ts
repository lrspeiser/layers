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
};

export type Registration = {
  layerIds: [string, string];
  commonWcs: boolean;
  commonFootprint: boolean;
  psfMatched: boolean;
  skyMatched: boolean;
  unitsMatched: boolean;
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
  title: string;
  priorAssumption: string;
  newEvidence: string;
  affectedInference: string;
  confidence: "unreviewed" | "candidate" | "supported" | "confirmed";
  systematicAlternatives: string[];
  recommendedFollowUp: string[];
};

export type Comparison = {
  id: string;
  layerIds: [string, string];
  status: "blocked" | "qa" | "published";
  registration?: Registration;
  measurements: DifferenceMeasurement[];
  inferences: Inference[];
  assumptionAudits: AssumptionAudit[];
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
    && comparison.registration.unitsMatched;
}
