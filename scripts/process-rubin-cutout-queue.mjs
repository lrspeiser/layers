#!/usr/bin/env node

import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { hostname } from "node:os";
import { list, put } from "@vercel/blob";

const root = resolve(import.meta.dirname, "..");
const queuePrefix = "rubin-on-demand/requests/";
const heartbeatPath = "rubin-on-demand/worker-status.json";
const workerId = `${hostname()}-${process.pid}`;
const maxAttempts = 3;
const staleProcessingMs = 30 * 60 * 1000;
const retryDelayMs = 15 * 60 * 1000;
const overlapIndex = JSON.parse(readFileSync(join(root, "public", "data", "coverage", "external-overlaps.json"), "utf8"));
const largeFootprintIndex = JSON.parse(readFileSync(join(root, "public", "data", "coverage", "large-footprint-resolution.json"), "utf8"));
const confirmedByTract = new Map(overlapIndex.tracts.map((row) => [Number(row[0]), [...row[1]]]));
for (const product of largeFootprintIndex.resolved ?? []) {
  for (const tract of product.confirmedRubinTractIds ?? []) {
    const current = confirmedByTract.get(Number(tract)) ?? [];
    if (!current.includes(product.surveyId)) current.push(product.surveyId);
    confirmedByTract.set(Number(tract), current);
  }
}

function loadEnv(path) {
  if (!existsSync(path)) return;
  for (const raw of readFileSync(path, "utf8").split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#") || !line.includes("=")) continue;
    const index = line.indexOf("=");
    const key = line.slice(0, index).trim();
    const value = line.slice(index + 1).trim().replace(/^['"]|['"]$/g, "");
    process.env[key] = value;
  }
}

loadEnv(join(root, ".env"));
loadEnv(join(root, ".env.local"));
if (!process.env.BLOB_READ_WRITE_TOKEN) throw new Error("BLOB_READ_WRITE_TOKEN is not configured");
if (!process.env.RUBIN_RSP_TOKEN) throw new Error("RUBIN_RSP_TOKEN is not configured");

const requestedTract = process.argv.find((value) => value.startsWith("--tract="))?.split("=", 2)[1];
const maxJobs = Number(process.argv.find((value) => value.startsWith("--max-jobs="))?.split("=", 2)[1] ?? "1");
const python = existsSync(join(root, ".venv", "Scripts", "python.exe"))
  ? join(root, ".venv", "Scripts", "python.exe")
  : "python";

function run(args) {
  const result = spawnSync(python, args, { cwd: root, env: process.env, stdio: "inherit" });
  if (result.status !== 0) throw new Error(`${args[0]} exited with code ${result.status}`);
}

async function writeJob(pathname, job) {
  job.updatedAt = new Date().toISOString();
  await put(pathname, JSON.stringify(job, null, 2), {
    access: "public", addRandomSuffix: false, allowOverwrite: true,
    cacheControlMaxAge: 60, contentType: "application/json",
  });
}

async function writeHeartbeat(status, details = {}) {
  await put(heartbeatPath, JSON.stringify({
    schemaVersion: "layers-rubin-worker-status-v1",
    workerId,
    status,
    updatedAt: new Date().toISOString(),
    cadenceMinutes: 5,
    maximumJobsPerRun: maxJobs,
    quotaPolicy: { sequential: true, rubinVoCutoutsPerMinute: 30, configuredLimitPerMinute: 35 },
    ...details,
  }, null, 2), {
    access: "public", addRandomSuffix: false, allowOverwrite: true,
    cacheControlMaxAge: 60, contentType: "application/json",
  });
}

async function upload(pathname, localPath, contentType) {
  const result = await put(pathname, readFileSync(localPath), {
    access: "public", addRandomSuffix: false, allowOverwrite: true,
    cacheControlMaxAge: 31_536_000, contentType,
  });
  return result.url;
}

async function processJob(blob, job) {
  const tract = Number(job.tract);
  const regionId = `dp2-tract-${tract}`;
  const work = join(root, "pipeline", "results", "on-demand", `tract-${tract}`);
  const discovery = join(work, "discovery");
  const pixels = join(work, "rubin-pixels");
  const legacy = join(work, "legacy");
  const legacyNormalized = join(work, "legacy-normalized");
  const comparisons = join(work, "comparison");
  const panstarrs = join(work, "panstarrs");
  const panstarrsComparisons = join(work, "panstarrs-comparison");
  const uvIrTime = join(work, "uv-ir-time");
  const gaia = join(work, "gaia");
  const sdssSpectrum = join(work, "sdss-spectrum");
  const privatePreview = join(root, "public", "private-preview", "on-demand", `tract-${tract}`);
  mkdirSync(work, { recursive: true });
  mkdirSync(privatePreview, { recursive: true });
  const regionPath = join(work, "region.json");
  const publicManifest = join(pixels, "public-manifest.json");
  const validationReport = join(pixels, "validation-report.json");
  writeFileSync(regionPath, JSON.stringify({
    schemaVersion: 1,
    regions: [{
      id: regionId, tract, center: job.center, sizeArcmin: job.sizeArcmin,
      confirmedSurveyIds: ["rubin-dp2", ...(confirmedByTract.get(tract) ?? [])],
    }],
  }, null, 2));

  job.status = "processing";
  job.attemptCount = Number(job.attemptCount ?? 0) + 1;
  job.lastAttemptAt = new Date().toISOString();
  job.worker = { id: workerId, leaseStartedAt: job.lastAttemptAt };
  delete job.error;
  await writeJob(blob.pathname, job);
  try {
    run([
      "pipeline/fetch_region_layers.py", "--regions", regionPath, "--output", discovery,
      "--cache", join(work, "discovery-cache"), "--public-manifest", join(discovery, "public-manifest.json"),
      "--mode", "discovery", "--only-survey", "rubin-dp2", "--cutout-size-arcmin", String(job.sizeArcmin),
    ]);
    run([
      "pipeline/acquire_dp2_pixels.py", "--plan", join(discovery, "acquisition-plan.json"),
      "--output", pixels, "--public-manifest", publicManifest, "--public-preview-root", privatePreview,
      "--only-region", regionId, "--max-regions", "1", "--cutout-size-arcmin", String(job.sizeArcmin),
      "--requests-per-minute", "30",
    ]);
    run([
      "pipeline/validate_dp2_pixels.py", "--manifest", join(pixels, "manifest.json"),
      "--public-manifest", publicManifest, "--report", validationReport, "--require-regions", "1",
    ]);

    const local = JSON.parse(readFileSync(join(pixels, "manifest.json"), "utf8"));
    const record = local.regions[0];
    if (record.status !== "complete" || !record.validation?.scienceReady || !record.preview?.path || !record.mosaic?.path) {
      throw new Error(record.error ?? "Rubin cutout failed science-ready validation");
    }
    const base = `rubin-on-demand/products/tract-${tract}`;
    const mosaicUrl = await upload(`${base}/rubin-dp2-${record.band}-4arcmin.fits`, join(root, record.mosaic.path), "application/fits");
    const previewUrl = await upload(`${base}/rubin-dp2-${record.band}-preview.png`, join(root, record.preview.path), "image/png");
    const publicManifestUrl = await upload(`${base}/public-manifest.json`, publicManifest, "application/json");
    const validationUrl = await upload(`${base}/validation.json`, validationReport, "application/json");
    const product = {
      schemaVersion: "layers-rubin-on-demand-product-v1", tract, center: job.center,
      band: record.band, status: "complete", scienceReady: true, comparisonReady: false,
      mosaicUrl, previewUrl, publicManifestUrl, validationUrl,
      validPixelFraction: record.validation.validPixelFraction,
      mosaicSha256: record.mosaic.sha256, previewSha256: record.preview.sha256,
      blockers: record.validation.comparisonBlockers,
    };
    const manifestBlob = await put(`${base}/product.json`, JSON.stringify(product, null, 2), {
      access: "public", addRandomSuffix: false, allowOverwrite: true,
      cacheControlMaxAge: 60, contentType: "application/json",
    });
    job.status = "complete";
    delete job.worker;
    job.science = {
      readiness: "science-ready", band: record.band, mosaicUrl, previewUrl,
      manifestUrl: manifestBlob.url, validationUrl,
      validPixelFraction: record.validation.validPixelFraction,
    };
    delete job.comparison;
    delete job.comparisonError;
    job.layers = [];
    job.layerErrors = [];
    job.catalogs = [];
    job.catalogErrors = [];
    job.spectra = [];
    job.spectrumSearches = [];
    job.spectrumErrors = [];

    if ((confirmedByTract.get(tract) ?? []).includes("legacy-surveys-dr10")) {
      try {
        const legacyPlan = join(legacy, "acquisition-plan.json");
        const legacyDetailed = join(legacyNormalized, "manifest.json");
        const legacyPublic = join(legacyNormalized, "public-manifest.json");
        run([
          "pipeline/fetch_region_layers.py", "--regions", regionPath, "--output", legacy,
          "--cache", join(work, "legacy-cache"), "--public-manifest", join(legacy, "public-manifest.json"),
          "--mode", "science", "--only-survey", "legacy-surveys-dr10", "--cutout-size-arcmin", String(job.sizeArcmin),
        ]);
        run([
          "pipeline/normalize_legacy_cutouts.py", "--plan", legacyPlan, "--output", legacyNormalized,
          "--detailed-manifest", legacyDetailed, "--public-manifest", legacyPublic,
          "--previews", join(privatePreview, "legacy"), "--band", "r",
        ]);
        run([
          "pipeline/build_selected_region_comparisons.py", "--rubin-manifest", join(pixels, "manifest.json"),
          "--legacy-manifest", legacyDetailed, "--panstarrs-manifest", join(work, "no-panstarrs.json"),
          "--products", comparisons, "--public-manifest", join(comparisons, "public-manifest.json"),
          "--previews", join(privatePreview, "comparison"),
        ]);
        const comparisonManifest = JSON.parse(readFileSync(join(comparisons, "manifest.json"), "utf8"));
        const comparison = comparisonManifest.regions[0];
        if (!comparison || !comparison.displayAlignmentAllowed || comparison.comparisonReady) {
          throw new Error(comparisonManifest.failures?.[0]?.error ?? "No validated common-grid Legacy product was produced");
        }
        const comparisonBase = `${base}/legacy-dr10`;
        const uploadPreview = async (name, key) => upload(`${comparisonBase}/${name}.png`, join(root, comparison.previews[key].path.replace(/^\//, "public/")), "image/png");
        const [rubinImageUrl, referenceImageUrl, coverageImageUrl, overlayImageUrl, alignedFitsUrl] = await Promise.all([
          uploadPreview("rubin-aligned", "rubin"),
          uploadPreview("legacy-reference", "reference"),
          uploadPreview("common-coverage", "coverage"),
          uploadPreview("position-overlay", "positionOverlay"),
          upload(`${comparisonBase}/rubin-legacy-display-grid.fits`, join(root, comparison.localFits.path), "application/fits"),
        ]);
        job.comparison = {
          readiness: "display-aligned", surveyId: "legacy-surveys-dr10", surveyName: "Legacy Survey", release: "DR10",
          rubinBand: comparison.rubinBand, referenceBand: comparison.referenceBand,
          referenceUnit: comparison.inputs.reference.unit, commonCoverageFraction: comparison.commonCoverageFraction,
          rubinImageUrl, referenceImageUrl, coverageImageUrl, overlayImageUrl, alignedFitsUrl,
          comparisonReady: false, blockers: comparison.comparisonBlockers,
        };
        job.layers.push({ ...job.comparison, family: "optical" });
      } catch (comparisonError) {
        job.comparisonError = comparisonError instanceof Error ? comparisonError.message : String(comparisonError);
        job.layerErrors.push({ surveyId: "legacy-surveys-dr10", error: job.comparisonError });
      }
    }

    if ((confirmedByTract.get(tract) ?? []).includes("panstarrs-dr2")) {
      try {
        const noLegacy = join(work, "no-legacy.json");
        writeFileSync(noLegacy, JSON.stringify({ regions: [] }, null, 2));
        run([
          "pipeline/fetch_panstarrs_gap_fill.py", "--regions", regionPath,
          "--result-root", panstarrs, "--preview-root", join(privatePreview, "panstarrs"),
          "--public-root", join(panstarrs, "public"),
        ]);
        run([
          "pipeline/build_selected_region_comparisons.py", "--rubin-manifest", join(pixels, "manifest.json"),
          "--legacy-manifest", noLegacy, "--panstarrs-manifest", join(panstarrs, "evidence", "manifest.json"),
          "--products", panstarrsComparisons, "--public-manifest", join(panstarrsComparisons, "public-manifest.json"),
          "--previews", join(privatePreview, "panstarrs-comparison"),
        ]);
        const comparisonManifest = JSON.parse(readFileSync(join(panstarrsComparisons, "manifest.json"), "utf8"));
        const comparison = comparisonManifest.regions[0];
        if (!comparison || comparison.referenceSurveyId !== "panstarrs-dr2" || !comparison.displayAlignmentAllowed || comparison.comparisonReady) {
          throw new Error(comparisonManifest.failures?.[0]?.error ?? "No validated common-grid Pan-STARRS product was produced");
        }
        const evidence = JSON.parse(readFileSync(join(panstarrs, "evidence", "manifest.json"), "utf8"));
        const evidenceRegion = evidence.regions[0];
        if (!evidenceRegion?.sourcePixelsValidated) throw new Error("Pan-STARRS source/support planes did not pass validation");
        const panstarrsBase = `${base}/panstarrs-i`;
        const uploadPreview = async (name, key) => upload(
          `${panstarrsBase}/${name}.png`,
          join(root, comparison.previews[key].path.replace(/^\//, "public/")),
          "image/png",
        );
        const [rubinImageUrl, referenceImageUrl, coverageImageUrl, overlayImageUrl, alignedFitsUrl] = await Promise.all([
          uploadPreview("rubin-aligned", "rubin"),
          uploadPreview("panstarrs-reference", "reference"),
          uploadPreview("common-coverage", "coverage"),
          uploadPreview("position-overlay", "positionOverlay"),
          upload(`${panstarrsBase}/rubin-panstarrs-display-grid.fits`, join(root, comparison.localFits.path), "application/fits"),
        ]);
        const scienceEntries = [];
        for (const product of evidenceRegion.products) {
          const scienceUrl = await upload(
            `${panstarrsBase}/${product.role}.fits`, join(root, product.localPath), "application/fits",
          );
          scienceEntries.push([`${product.role}Url`, scienceUrl]);
        }
        job.layers.push({
          readiness: "display-aligned", surveyId: "panstarrs-dr2", surveyName: "Pan-STARRS1", family: "optical",
          release: evidenceRegion.release, rubinBand: comparison.rubinBand, referenceBand: comparison.referenceBand,
          referenceUnit: comparison.inputs.reference.unit, commonCoverageFraction: comparison.commonCoverageFraction,
          rubinImageUrl, referenceImageUrl, coverageImageUrl, overlayImageUrl, alignedFitsUrl,
          scienceAssets: Object.fromEntries(scienceEntries), comparisonReady: false,
          blockers: comparison.comparisonBlockers,
        });
      } catch (layerError) {
        job.layerErrors.push({ surveyId: "panstarrs-dr2", error: layerError instanceof Error ? layerError.message : String(layerError) });
      }
    }

    try {
      const uvPublic = join(uvIrTime, "public-manifest.json");
      run([
        "pipeline/fetch_uv_ir_time_pixels.py", "--regions", regionPath, "--work", uvIrTime,
        "--public-manifest", uvPublic, "--previews", join(privatePreview, "uv-ir-time"),
        "--rubin-manifest", join(pixels, "manifest.json"),
        "--only-survey", "unwise", "--only-survey", "2mass", "--only-survey", "ztf-dr",
        "--only-survey", "galex-gr6-7",
      ]);
      const uvManifest = JSON.parse(readFileSync(join(uvIrTime, "manifest.json"), "utf8"));
      const uvRegion = uvManifest.regions.find((item) => item.regionId === regionId);
      const unwise = uvRegion?.surveys?.unwise;
      const w1 = unwise?.bands?.W1;
      if (!w1 || unwise.status !== "available" || !w1.alignment) {
        throw new Error(unwise?.error ?? "No validated unWISE W1 alignment was produced");
      }
      const unwiseDirectory = join(uvIrTime, "products", regionId, "unwise");
      const unwiseBase = `${base}/unwise-w1`;
      const fromPublic = (pathname) => join(root, "public", pathname.replace(/^\//, ""));
      const [referenceImageUrl, rubinImageUrl, coverageImageUrl, overlayImageUrl, imageUrl, inverseVarianceUrl, exposureCountUrl, sampleStdDevUrl] = await Promise.all([
        upload(`${unwiseBase}/unwise-w1-preview.jpg`, fromPublic(w1.assets.preview.path), "image/jpeg"),
        upload(`${unwiseBase}/rubin-aligned.jpg`, fromPublic(w1.alignment.alignedRubinPreviewPath), "image/jpeg"),
        upload(`${unwiseBase}/common-coverage.png`, fromPublic(w1.alignment.coveragePreviewPath), "image/png"),
        upload(`${unwiseBase}/position-overlay.jpg`, fromPublic(w1.alignment.overlayPreviewPath), "image/jpeg"),
        upload(`${unwiseBase}/${w1.assets.image.filename}`, join(unwiseDirectory, w1.assets.image.filename), "application/fits"),
        upload(`${unwiseBase}/${w1.assets.inverseVariance.filename}`, join(unwiseDirectory, w1.assets.inverseVariance.filename), "application/gzip"),
        upload(`${unwiseBase}/${w1.assets.coverage.filename}`, join(unwiseDirectory, w1.assets.coverage.filename), "application/gzip"),
        upload(`${unwiseBase}/${w1.assets.sampleStdDev.filename}`, join(unwiseDirectory, w1.assets.sampleStdDev.filename), "application/gzip"),
      ]);
      job.layers.push({
        readiness: "display-aligned", surveyId: "unwise", surveyName: "unWISE", family: "uv-ir",
        release: "AllWISE unblurred coadds", rubinBand: w1.alignment.rubinBand,
        referenceBand: "W1", referenceUnit: w1.unit,
        commonCoverageFraction: w1.alignment.commonValidPixelFraction,
        rubinImageUrl, referenceImageUrl, coverageImageUrl, overlayImageUrl,
        scienceAssets: { imageUrl, inverseVarianceUrl, exposureCountUrl, sampleStdDevUrl },
        comparisonReady: false,
        blockers: [
          "PSF matching", "bandpass and color-term transfer", "background matching",
          "astrometric residual QA", "correlated-noise and mask propagation", "injection/recovery QA",
        ],
      });
    } catch (layerError) {
      job.layerErrors.push({ surveyId: "unwise", error: layerError instanceof Error ? layerError.message : String(layerError) });
    }
    try {
      const uvManifest = JSON.parse(readFileSync(join(uvIrTime, "manifest.json"), "utf8"));
      const uvRegion = uvManifest.regions.find((item) => item.regionId === regionId);
      const twoMass = uvRegion?.surveys?.["2mass"];
      const ks = twoMass?.bands?.Ks;
      if (!ks || twoMass.status !== "available" || !ks.alignment) {
        throw new Error(twoMass?.error ?? "No validated 2MASS Ks alignment was produced");
      }
      const twoMassDirectory = join(uvIrTime, "products", regionId, "2mass");
      const twoMassBase = `${base}/2mass`;
      const fromPublic = (pathname) => join(root, "public", pathname.replace(/^\//, ""));
      const [referenceImageUrl, rubinImageUrl, coverageImageUrl, overlayImageUrl] = await Promise.all([
        upload(`${twoMassBase}/2mass-ks-preview.jpg`, fromPublic(ks.assets.preview.path), "image/jpeg"),
        upload(`${twoMassBase}/rubin-aligned.jpg`, fromPublic(ks.alignment.alignedRubinPreviewPath), "image/jpeg"),
        upload(`${twoMassBase}/common-coverage.png`, fromPublic(ks.alignment.coveragePreviewPath), "image/png"),
        upload(`${twoMassBase}/position-overlay.jpg`, fromPublic(ks.alignment.overlayPreviewPath), "image/jpeg"),
      ]);
      const scienceEntries = [];
      for (const band of ["J", "H", "Ks"]) {
        const record = twoMass.bands?.[band];
        if (!record?.assets?.image?.filename) continue;
        const slug = band.toLowerCase();
        const [imageUrl, previewUrl] = await Promise.all([
          upload(`${twoMassBase}/${record.assets.image.filename}`, join(twoMassDirectory, record.assets.image.filename), "application/fits"),
          upload(`${twoMassBase}/2mass-${slug}-preview.jpg`, fromPublic(record.assets.preview.path), "image/jpeg"),
        ]);
        scienceEntries.push([`${slug}ImageUrl`, imageUrl], [`${slug}PreviewUrl`, previewUrl]);
      }
      job.layers.push({
        readiness: "display-aligned", surveyId: "2mass", surveyName: "2MASS", family: "uv-ir",
        release: "All-Sky Atlas", rubinBand: ks.alignment.rubinBand,
        referenceBand: "Ks", referenceUnit: ks.unit,
        commonCoverageFraction: ks.alignment.commonValidPixelFraction,
        rubinImageUrl, referenceImageUrl, coverageImageUrl, overlayImageUrl,
        scienceAssets: Object.fromEntries(scienceEntries),
        comparisonReady: false,
        blockers: [
          "No registered per-pixel uncertainty plane or artifact mask from the Atlas cutout service",
          "PSF matching", "bandpass and color-term transfer", "background matching",
          "astrometric residual QA", "correlated-noise and mask propagation", "injection/recovery QA",
        ],
      });
    } catch (layerError) {
      job.layerErrors.push({ surveyId: "2mass", error: layerError instanceof Error ? layerError.message : String(layerError) });
    }
    try {
      const uvManifest = JSON.parse(readFileSync(join(uvIrTime, "manifest.json"), "utf8"));
      const uvRegion = uvManifest.regions.find((item) => item.regionId === regionId);
      const ztf = uvRegion?.surveys?.["ztf-dr"];
      const reference = ztf?.reference;
      if (!reference || ztf.status !== "available" || !reference.alignment) {
        throw new Error(ztf?.error ?? "No validated ZTF reference alignment was produced");
      }
      const ztfDirectory = join(uvIrTime, "products", regionId, "ztf");
      const ztfBase = `${base}/ztf-reference`;
      const fromPublic = (pathname) => join(root, "public", pathname.replace(/^\//, ""));
      const [referenceImageUrl, rubinImageUrl, coverageImageUrl, overlayImageUrl] = await Promise.all([
        upload(`${ztfBase}/ztf-reference.jpg`, fromPublic(reference.assets.preview.path), "image/jpeg"),
        upload(`${ztfBase}/rubin-aligned.jpg`, fromPublic(reference.alignment.alignedRubinPreviewPath), "image/jpeg"),
        upload(`${ztfBase}/common-coverage.png`, fromPublic(reference.alignment.coveragePreviewPath), "image/png"),
        upload(`${ztfBase}/position-overlay.jpg`, fromPublic(reference.alignment.overlayPreviewPath), "image/jpeg"),
      ]);
      const scienceEntries = [];
      for (const role of ["image", "uncertainty", "coverage"]) {
        const asset = reference.assets?.[role];
        if (!asset?.filename) continue;
        const url = await upload(`${ztfBase}/${asset.filename}`, join(ztfDirectory, asset.filename), "application/fits");
        scienceEntries.push([`${role}Url`, url]);
      }
      const timeSeries = ztf.timeSeries;
      if (timeSeries?.status === "available" && timeSeries.artifact?.filename) {
        const catalogUrl = await upload(
          `${ztfBase}/ztf-lightcurves.csv`, join(uvIrTime, "cache", regionId, timeSeries.artifact.filename), "text/csv",
        );
        scienceEntries.push(["lightCurveCatalogUrl", catalogUrl]);
      }
      job.layers.push({
        readiness: "display-aligned", surveyId: "ztf-dr", surveyName: "Zwicky Transient Facility", family: "time-domain",
        release: "Public archive reference products; light-curve response release unstamped",
        rubinBand: reference.alignment.rubinBand, referenceBand: reference.band, referenceUnit: reference.unit,
        commonCoverageFraction: reference.alignment.commonValidPixelFraction,
        rubinImageUrl, referenceImageUrl, coverageImageUrl, overlayImageUrl,
        scienceAssets: Object.fromEntries(scienceEntries), comparisonReady: false,
        blockers: [
          "The reference coadd is static; variability evidence is only in the separate epoch catalog",
          "The light-curve response does not stamp a release number", "epoch-quality filtering",
          "PSF matching", "bandpass transfer", "background matching", "resampling covariance",
        ],
      });
    } catch (layerError) {
      if ((confirmedByTract.get(tract) ?? []).includes("ztf-dr")) {
        job.layerErrors.push({ surveyId: "ztf-dr", error: layerError instanceof Error ? layerError.message : String(layerError) });
      }
    }
    try {
      const uvManifest = JSON.parse(readFileSync(join(uvIrTime, "manifest.json"), "utf8"));
      const uvRegion = uvManifest.regions.find((item) => item.regionId === regionId);
      const galex = uvRegion?.surveys?.["galex-gr6-7"];
      const referenceBand = ["NUV", "FUV"].find((band) => galex?.bands?.[band]?.alignment);
      const reference = referenceBand ? galex.bands[referenceBand] : null;
      if (!reference || galex.status !== "available") {
        throw new Error(galex?.error ?? "No validated GALEX alignment was produced");
      }
      const galexDirectory = join(uvIrTime, "products", regionId, "galex");
      const galexBase = `${base}/galex`;
      const fromPublic = (pathname) => join(root, "public", pathname.replace(/^\//, ""));
      const [referenceImageUrl, rubinImageUrl, coverageImageUrl, overlayImageUrl] = await Promise.all([
        upload(`${galexBase}/galex-${referenceBand.toLowerCase()}-preview.jpg`, fromPublic(reference.preview.path), "image/jpeg"),
        upload(`${galexBase}/rubin-aligned.jpg`, fromPublic(reference.alignment.alignedRubinPreviewPath), "image/jpeg"),
        upload(`${galexBase}/common-coverage.png`, fromPublic(reference.alignment.coveragePreviewPath), "image/png"),
        upload(`${galexBase}/position-overlay.jpg`, fromPublic(reference.alignment.overlayPreviewPath), "image/jpeg"),
      ]);
      const scienceEntries = [];
      for (const band of ["FUV", "NUV"]) {
        const record = galex.bands?.[band];
        if (record?.status !== "available" || !record.standardProduct?.filename) continue;
        const slug = band.toLowerCase();
        const [fitsUrl, previewUrl] = await Promise.all([
          upload(`${galexBase}/${record.standardProduct.filename}`, join(galexDirectory, record.standardProduct.filename), "application/fits"),
          upload(`${galexBase}/galex-${slug}-preview.jpg`, fromPublic(record.preview.path), "image/jpeg"),
        ]);
        scienceEntries.push([`${slug}FitsUrl`, fitsUrl], [`${slug}PreviewUrl`, previewUrl]);
      }
      job.layers.push({
        readiness: "display-aligned", surveyId: "galex-gr6-7", surveyName: "GALEX", family: "uv-ir",
        release: "GR6/GR7", rubinBand: reference.alignment.rubinBand, referenceBand,
        referenceUnit: reference.unit, commonCoverageFraction: reference.alignment.commonValidPixelFraction,
        rubinImageUrl, referenceImageUrl, coverageImageUrl, overlayImageUrl,
        scienceAssets: Object.fromEntries(scienceEntries), comparisonReady: false,
        blockers: [
          "GALEX photon-counting masks and low-response edges require survey-specific QA",
          "PSF matching", "bandpass and color-term transfer", "background matching",
          "astrometric residual QA", "correlated-noise propagation", "injection/recovery QA",
        ],
      });
    } catch (layerError) {
      if ((confirmedByTract.get(tract) ?? []).includes("galex-gr6-7")) {
        job.layerErrors.push({ surveyId: "galex-gr6-7", error: layerError instanceof Error ? layerError.message : String(layerError) });
      }
    }
    if ((confirmedByTract.get(tract) ?? []).includes("gaia-dr3")) {
      try {
        run(["pipeline/fetch_gaia_tract.py", "--regions", regionPath, "--output", gaia]);
        const gaiaManifest = JSON.parse(readFileSync(join(gaia, "manifest.json"), "utf8"));
        const gaiaRecord = gaiaManifest.regions?.[0];
        if (!gaiaRecord || gaiaRecord.status !== "available") {
          throw new Error("Gaia returned no catalogue rows for this bounded tract center");
        }
        const catalogUrl = await upload(`${base}/gaia-dr3/gaia-dr3.csv`, gaiaRecord.localPath, "text/csv");
        job.catalogs.push({
          readiness: "catalog-evidence", surveyId: "gaia-dr3", surveyName: "Gaia", family: "astrometry",
          release: "DR3", recordCount: gaiaRecord.recordCount,
          summary: {
            sourcesWithProperMotion: gaiaRecord.sourcesWithProperMotion,
            significantProperMotionCount: gaiaRecord.significantProperMotionCount,
            significantParallaxCount: gaiaRecord.significantParallaxCount,
            foregroundScreeningCandidateCount: gaiaRecord.foregroundScreeningCandidateCount,
            elevatedRuweCount: gaiaRecord.elevatedRuweCount,
          },
          units: gaiaRecord.units, catalogUrl, caveats: gaiaRecord.caveats,
        });
      } catch (catalogError) {
        job.catalogErrors.push({ surveyId: "gaia-dr3", error: catalogError instanceof Error ? catalogError.message : String(catalogError) });
      }
    }
    if ((confirmedByTract.get(tract) ?? []).includes("sdss-dr19")) {
      try {
        run([
          "pipeline/fetch_sdss_spectrum_tract.py", "--regions", regionPath,
          "--output", sdssSpectrum, "--radius-arcmin", "60",
        ]);
        const sdssManifest = JSON.parse(readFileSync(join(sdssSpectrum, "manifest.json"), "utf8"));
        const sdssRecord = sdssManifest.regions?.[0];
        if (!sdssRecord) throw new Error("SDSS spectrum connector returned no tract result");
        if (sdssRecord.status === "none") {
          job.spectrumSearches.push({
            surveyId: "sdss-dr19", surveyName: "SDSS", release: sdssRecord.release,
            status: "none", radiusArcmin: sdssRecord.radiusArcmin, reason: sdssRecord.reason,
          });
        } else if (sdssRecord.status === "available") {
          const spectrumBase = `${base}/sdss-dr19-spectrum`;
          const [fitsUrl, samplesUrl, spectrumPreviewUrl] = await Promise.all([
            upload(`${spectrumBase}/${sdssRecord.selection.sasFile}`, sdssRecord.artifacts.fits.localPath, "application/fits"),
            upload(`${spectrumBase}/wavelength-flux.csv`, sdssRecord.artifacts.samplesCsv.localPath, "text/csv"),
            upload(`${spectrumBase}/spectrum-preview.png`, sdssRecord.artifacts.preview.localPath, "image/png"),
          ]);
          job.spectra.push({
            readiness: "spectrum-evidence", surveyId: "sdss-dr19", surveyName: "SDSS", family: "spectroscopy",
            release: sdssRecord.release, instrument: sdssRecord.selection.instrument,
            objectClass: sdssRecord.spectrum.objectClass, objectSubclass: sdssRecord.spectrum.objectSubclass,
            sampleCount: sdssRecord.spectrum.samples, validFluxSampleCount: sdssRecord.spectrum.validFluxSamples,
            wavelengthRangeAngstrom: sdssRecord.spectrum.wavelengthRangeAngstrom,
            redshift: sdssRecord.spectrum.redshift, redshiftError: sdssRecord.spectrum.redshiftError,
            redshiftWarning: sdssRecord.spectrum.redshiftWarning,
            separationArcmin: sdssRecord.selection.separationArcmin,
            previewUrl: spectrumPreviewUrl, fitsUrl, samplesUrl, caveats: sdssRecord.caveats,
          });
        } else {
          throw new Error(`Unexpected SDSS spectrum status: ${sdssRecord.status}`);
        }
      } catch (spectrumError) {
        job.spectrumErrors.push({ surveyId: "sdss-dr19", error: spectrumError instanceof Error ? spectrumError.message : String(spectrumError) });
      }
    }
    if (!job.layerErrors.length) delete job.layerErrors;
    if (!job.catalogErrors.length) delete job.catalogErrors;
    if (!job.spectrumErrors.length) delete job.spectrumErrors;
    if (!job.spectrumSearches.length) delete job.spectrumSearches;
    await writeJob(blob.pathname, job);
    process.stdout.write(`Completed Rubin on-demand tract ${tract}\n`);
  } catch (error) {
    job.status = "error";
    job.error = error instanceof Error ? error.message : String(error);
    delete job.worker;
    await writeJob(blob.pathname, job);
    process.stderr.write(`Failed Rubin on-demand tract ${tract}: ${job.error}\n`);
    return false;
  }
  return true;
}

await writeHeartbeat("scanning");
const result = await list({ prefix: queuePrefix, limit: 1000 });
const pending = [];
const now = Date.now();
for (const blob of result.blobs) {
  const response = await fetch(blob.url, { cache: "no-store" });
  if (!response.ok) continue;
  const job = await response.json();
  if (requestedTract && String(job.tract) !== requestedTract) continue;
  const age = now - Date.parse(job.updatedAt ?? job.requestedAt ?? 0);
  const staleProcessing = job.status === "processing" && age >= staleProcessingMs;
  const retryableError = job.status === "error" && Number(job.attemptCount ?? 0) < maxAttempts && (requestedTract || age >= retryDelayMs);
  const requestedEnrichment = requestedTract && job.status === "complete" && (
    !job.comparison || !job.layers?.some((layer) => layer.surveyId === "unwise") ||
    !job.layers?.some((layer) => layer.surveyId === "2mass") ||
    ((confirmedByTract.get(Number(job.tract)) ?? []).includes("panstarrs-dr2") &&
      !job.layers?.some((layer) => layer.surveyId === "panstarrs-dr2")) ||
    ((confirmedByTract.get(Number(job.tract)) ?? []).includes("ztf-dr") &&
      !job.layers?.some((layer) => layer.surveyId === "ztf-dr")) ||
    ((confirmedByTract.get(Number(job.tract)) ?? []).includes("galex-gr6-7") &&
      !job.layers?.some((layer) => layer.surveyId === "galex-gr6-7")) ||
    ((confirmedByTract.get(Number(job.tract)) ?? []).includes("gaia-dr3") &&
      !job.catalogs?.some((catalog) => catalog.surveyId === "gaia-dr3")) ||
    ((confirmedByTract.get(Number(job.tract)) ?? []).includes("sdss-dr19") &&
      !job.spectra?.some((spectrum) => spectrum.surveyId === "sdss-dr19") &&
      !job.spectrumSearches?.some((search) => search.surveyId === "sdss-dr19"))
  );
  const requestedRecovery = requestedTract && job.status === "processing";
  if (job.status === "queued" || staleProcessing || retryableError || requestedEnrichment || requestedRecovery) pending.push({ blob, job });
}
pending.sort((a, b) => String(a.job.requestedAt).localeCompare(String(b.job.requestedAt)));
let succeeded = 0;
let failed = 0;
for (const item of pending.slice(0, Math.max(0, maxJobs))) {
  if (await processJob(item.blob, item.job)) succeeded += 1;
  else failed += 1;
}
await writeHeartbeat("idle", {
  scannedJobCount: result.blobs.length,
  discoveredPendingJobCount: pending.length,
  pendingJobCount: Math.max(0, pending.length - Math.min(pending.length, maxJobs)),
  processedJobCount: Math.min(pending.length, maxJobs),
  succeededJobCount: succeeded,
  failedJobCount: failed,
});
process.stdout.write(`Queue scan complete: ${pending.length} pending, ${succeeded} succeeded, ${failed} failed\n`);
