export type MetricClassification = "large" | "above" | "expected";

export type AtlasMetric = {
  label: string;
  value: string;
  uncertainty: string;
  expectedRange: string;
  significanceSigma: number;
  classification: MetricClassification;
};

export type AtlasManifest = {
  schemaVersion: 1;
  objectId: string;
  release: "EDP2";
  verified: boolean;
  center: { raDeg: number; decDeg: number };
  field: {
    widthArcmin: number;
    heightArcmin: number;
    pixelScaleArcsec: number;
  };
  images: {
    rubin: {
      rgb: string;
      diffuse?: string;
      bands: Partial<Record<"u" | "g" | "r" | "i" | "z" | "y", string>>;
    };
    legacy: {
      rgb: string;
      bands?: Partial<Record<"u" | "g" | "r" | "i" | "z" | "y", string>>;
    };
  };
  registration: {
    commonWcs: boolean;
    psfMatched: boolean;
    skyMatched: boolean;
    maxResidualArcsec: number;
    qaThresholdArcsec: number;
  };
  provenance: {
    datasetType: "deep_coadd";
    collection: "dp2";
    butlerDatasetIds: string[];
    createdAt: string;
    sourceSha256: Record<string, string>;
  };
  metrics?: AtlasMetric[];
};

export const manifestUrl = (slug: string) => `/atlas/${slug}/manifest.json`;
