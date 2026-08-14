import { createHash } from "node:crypto";
import { BlobNotFoundError, head, put } from "@vercel/blob";
import * as healpix from "@hscmap/healpix";
import footprintData from "@/public/data/coverage/rubin-dp2-footprint.json";

const HIPS_ORDER = 11;
const HIPS_BASE = "https://data.lsst.cloud/api/hips/v2/dp2/color_gri";
const CACHE_PREFIX = "rubin-on-demand";

type FootprintRow = [number, [number, number], unknown, number, number[]];

export type RubinCutoutJob = {
  schemaVersion: "layers-rubin-on-demand-v1";
  tract: number;
  center: [number, number];
  sizeArcmin: number;
  status: "queued" | "processing" | "complete" | "error";
  requestedAt: string;
  updatedAt: string;
  display?: {
    kind: "rubin-dp2-hips-tile";
    readiness: "display-only";
    previewUrl: string;
    pathname: string;
    sha256: string;
    hipsOrder: number;
    hipsPixel: number;
    note: string;
  };
  previewError?: string;
  attemptCount?: number;
  lastAttemptAt?: string;
  worker?: { id: string; leaseStartedAt: string };
  science?: {
    readiness: "science-ready";
    band: string;
    mosaicUrl: string;
    previewUrl: string;
    manifestUrl: string;
    validationUrl: string;
    validPixelFraction: number;
  };
  comparison?: {
    readiness: "display-aligned";
    surveyId: "legacy-surveys-dr10";
    surveyName: "Legacy Survey";
    release: "DR10";
    rubinBand: string;
    referenceBand: string;
    referenceUnit: string;
    commonCoverageFraction: number;
    rubinImageUrl: string;
    referenceImageUrl: string;
    coverageImageUrl: string;
    overlayImageUrl: string;
    alignedFitsUrl: string;
    comparisonReady: false;
    blockers: string[];
  };
  comparisonError?: string;
  layers?: Array<{
    readiness: "display-aligned";
    surveyId: string;
    surveyName: string;
    family: string;
    release: string;
    rubinBand: string;
    referenceBand: string;
    referenceUnit: string;
    commonCoverageFraction: number;
    rubinImageUrl: string;
    referenceImageUrl: string;
    coverageImageUrl: string;
    overlayImageUrl: string;
    scienceAssets?: Record<string, string>;
    comparisonReady: false;
    blockers: string[];
  }>;
  catalogs?: Array<{
    readiness: "catalog-evidence";
    surveyId: string;
    surveyName: string;
    family: string;
    release: string;
    recordCount: number;
    summary: Record<string, number>;
    units: Record<string, string>;
    catalogUrl: string;
    caveats: string[];
  }>;
  spectra?: Array<{
    readiness: "spectrum-evidence";
    surveyId: string;
    surveyName: string;
    family: "spectroscopy";
    release: string;
    instrument: string;
    objectClass: string;
    objectSubclass: string;
    sampleCount: number;
    validFluxSampleCount: number;
    wavelengthRangeAngstrom: [number, number];
    redshift: number | null;
    redshiftError: number | null;
    redshiftWarning: number | null;
    separationArcmin: number;
    previewUrl: string;
    fitsUrl: string;
    samplesUrl: string;
    caveats: string[];
  }>;
  spectrumSearches?: Array<{
    surveyId: string;
    surveyName: string;
    release: string;
    status: "none";
    radiusArcmin: number;
    reason: string;
  }>;
  layerErrors?: Array<{ surveyId: string; error: string }>;
  catalogErrors?: Array<{ surveyId: string; error: string }>;
  spectrumErrors?: Array<{ surveyId: string; error: string }>;
  error?: string;
};

export type RubinWorkerStatus = {
  schemaVersion: "layers-rubin-worker-status-v1";
  workerId: string;
  status: "scanning" | "idle";
  updatedAt: string;
  cadenceMinutes: number;
  maximumJobsPerRun: number;
  scannedJobCount?: number;
  pendingJobCount?: number;
  processedJobCount?: number;
  succeededJobCount?: number;
  failedJobCount?: number;
};

const tractRows = footprintData.tracts as unknown as FootprintRow[];
const tractMap = new Map(tractRows.map((row) => [row[0], row]));

function jobPath(tract: number) {
  return `${CACHE_PREFIX}/requests/tract-${tract}.json`;
}

function previewPath(tract: number, pixel: number) {
  return `${CACHE_PREFIX}/previews/tract-${tract}-gri-o${HIPS_ORDER}-p${pixel}.png`;
}

export function getTractCenter(tract: number): [number, number] | null {
  return tractMap.get(tract)?.[1] ?? null;
}

async function readPublicJson<T>(pathname: string): Promise<T | null> {
  try {
    const metadata = await head(pathname);
    const response = await fetch(metadata.url, { cache: "no-store" });
    if (!response.ok) return null;
    return await response.json() as T;
  } catch (error) {
    if (error instanceof BlobNotFoundError) return null;
    throw error;
  }
}

export async function getRubinCutoutJob(tract: number): Promise<RubinCutoutJob | null> {
  if (!getTractCenter(tract) || !process.env.BLOB_READ_WRITE_TOKEN) return null;
  return readPublicJson<RubinCutoutJob>(jobPath(tract));
}

export async function getRubinWorkerStatus(): Promise<RubinWorkerStatus | null> {
  if (!process.env.BLOB_READ_WRITE_TOKEN) return null;
  return readPublicJson<RubinWorkerStatus>(`${CACHE_PREFIX}/worker-status.json`);
}

export function getRubinHipsPixel(raDeg: number, decDeg: number) {
  const nside = healpix.order2nside(HIPS_ORDER);
  const theta = Math.PI / 2 - decDeg * Math.PI / 180;
  const phi = ((raDeg % 360) + 360) % 360 * Math.PI / 180;
  return healpix.ang2pix_nest(nside, theta, phi);
}

async function cacheRubinHipsTile(tract: number, center: [number, number]) {
  const pixel = getRubinHipsPixel(center[0], center[1]);
  const pathname = previewPath(tract, pixel);
  try {
    const cached = await head(pathname);
    return { url: cached.url, pathname, sha256: cached.contentDisposition?.match(/sha256=([a-f0-9]+)/)?.[1] ?? "cached", pixel };
  } catch (error) {
    if (!(error instanceof BlobNotFoundError)) throw error;
  }

  const token = process.env.RUBIN_RSP_TOKEN;
  if (!token) throw new Error("Rubin server credential is not configured");
  const directory = Math.floor(pixel / 10_000) * 10_000;
  const upstream = `${HIPS_BASE}/Norder${HIPS_ORDER}/Dir${directory}/Npix${pixel}.png`;
  const response = await fetch(upstream, {
    headers: { Authorization: `Bearer ${token}`, Accept: "image/png" },
    cache: "no-store",
  });
  if (!response.ok) throw new Error(`Rubin HiPS returned HTTP ${response.status}`);
  const bytes = Buffer.from(await response.arrayBuffer());
  if (bytes.length < 8 || bytes.subarray(0, 8).toString("hex") !== "89504e470d0a1a0a") {
    throw new Error("Rubin HiPS response is not a PNG tile");
  }
  if (bytes.length < 4_096) {
    throw new Error("Rubin HiPS tile has no meaningful display pixels at this tract center");
  }
  const sha256 = createHash("sha256").update(bytes).digest("hex");
  const blob = await put(pathname, bytes, {
    access: "public",
    addRandomSuffix: false,
    allowOverwrite: false,
    cacheControlMaxAge: 31_536_000,
    contentType: "image/png",
  });
  return { url: blob.url, pathname, sha256, pixel };
}

export async function queueRubinCutout(tract: number): Promise<RubinCutoutJob> {
  const center = getTractCenter(tract);
  if (!center) throw new Error("Unknown Rubin tract");
  if (!process.env.BLOB_READ_WRITE_TOKEN) throw new Error("Persistent cache is not configured");

  const existing = await getRubinCutoutJob(tract);
  if (existing?.status === "complete" || existing?.status === "processing" || existing?.status === "queued") {
    return existing;
  }

  const now = new Date().toISOString();
  const job: RubinCutoutJob = {
    schemaVersion: "layers-rubin-on-demand-v1",
    tract,
    center,
    sizeArcmin: 4,
    status: "queued",
    requestedAt: now,
    updatedAt: now,
  };
  try {
    const display = await cacheRubinHipsTile(tract, center);
    job.display = {
      kind: "rubin-dp2-hips-tile",
      readiness: "display-only",
      previewUrl: display.url,
      pathname: display.pathname,
      sha256: display.sha256,
      hipsOrder: HIPS_ORDER,
      hipsPixel: display.pixel,
      note: "Authenticated Rubin DP2 gri HiPS pixels. This immediate preview is not the science-ready MaskedImage mosaic.",
    };
  } catch (error) {
    job.previewError = error instanceof Error ? error.message : "Rubin HiPS preview unavailable";
  }
  await put(jobPath(tract), JSON.stringify(job, null, 2), {
    access: "public",
    addRandomSuffix: false,
    allowOverwrite: true,
    cacheControlMaxAge: 60,
    contentType: "application/json",
  });
  return job;
}

export const rubinOnDemandPaths = { jobPath, previewPath };
