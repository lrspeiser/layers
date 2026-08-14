"use client";

import { useCallback, useEffect, useState } from "react";
import Image from "next/image";
import styles from "./OnDemandRubinCutout.module.css";
import type { RubinWorkerStatus } from "@/lib/rubin-on-demand";

export type OnDemandRubinJob = {
  tract: number;
  status: "not-requested" | "queued" | "processing" | "complete" | "error";
  display?: { previewUrl: string; note: string; sha256: string };
  science?: { previewUrl: string; mosaicUrl: string; band: string; validPixelFraction: number };
  comparison?: { surveyName: string; commonCoverageFraction: number };
  layers?: Array<{
    surveyId: string;
    surveyName: string;
    referenceBand: string;
    scienceAssets?: Record<string, string>;
  }>;
  catalogs?: Array<{
    surveyId: string;
    surveyName: string;
    release: string;
    recordCount: number;
    summary: Record<string, number>;
    units: Record<string, string>;
    catalogUrl: string;
    caveats: string[];
  }>;
  spectra?: Array<{
    surveyId: string;
    surveyName: string;
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
  comparisonError?: string;
  layerErrors?: Array<{ surveyId: string; error: string }>;
  catalogErrors?: Array<{ surveyId: string; error: string }>;
  spectrumErrors?: Array<{ surveyId: string; error: string }>;
  previewError?: string;
  error?: string;
};

function assetLabel(key: string) {
  return key
    .replace(/Url$/, "")
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/^./, (value) => value.toUpperCase());
}

export function OnDemandRubinCutout({ tract, initialJob = null, workerStatus = null }: { tract: number; initialJob?: OnDemandRubinJob | null; workerStatus?: RubinWorkerStatus | null }) {
  const [job, setJob] = useState<OnDemandRubinJob>(initialJob ?? { tract, status: "not-requested" });
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    const response = await fetch(`/api/tracts/${tract}/cutout`, { cache: "no-store" });
    if (response.ok) setJob(await response.json());
  }, [tract]);

  useEffect(() => {
    if (job.status !== "queued" && job.status !== "processing") return;
    const timer = window.setInterval(() => void refresh(), 10_000);
    return () => window.clearInterval(timer);
  }, [job.status, refresh]);

  async function requestCutout() {
    setBusy(true);
    try {
      const response = await fetch(`/api/tracts/${tract}/cutout`, { method: "POST" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error ?? "Cutout request failed");
      setJob(payload);
    } catch (error) {
      setJob({ tract, status: "error", error: error instanceof Error ? error.message : "Cutout request failed" });
    } finally {
      setBusy(false);
    }
  }

  const preview = job.science?.previewUrl ?? job.display?.previewUrl;
  return (
    <div className={styles.card}>
      {preview && <Image src={preview} width={512} height={512} unoptimized alt={`Real Rubin DP2 pixels requested for tract ${tract}`} />}
      <div>
        <span>ON-DEMAND RUBIN CACHE</span>
        <h3>{job.science ? `Science-ready ${job.science.band}-band mosaic` : job.display ? "Authenticated Rubin pixels cached" : "Fetch the first real image"}</h3>
        {job.science ? (
          <p>{(job.science.validPixelFraction * 100).toFixed(2)}% validated pixels with IMAGE, VARIANCE, MASK, WCS, units, and checksums. Black shapes mark masked NO_DATA or finite-support gaps, not dark sky objects. <a href={job.science.mosaicUrl}>Download the FITS mosaic</a>.</p>
        ) : job.display ? (
          <p>{job.display.note} The 4 arcmin MaskedImage job is <strong>{job.status}</strong> for local validation and persistent caching.</p>
        ) : (
          <p>This fetches an authenticated Rubin DP2 gri HiPS tile immediately and creates a bounded 4 arcmin science-cutout job. It never downloads the whole tract.</p>
        )}
        {job.error && <p className={styles.error}>{job.error}</p>}
        {job.previewError && job.status !== "not-requested" && <p>The instant color preview was unavailable, but the science-cutout job is still {job.status}.</p>}
        {job.comparisonError && <p className={styles.error}>Historical overlay: {job.comparisonError}</p>}
        {job.layerErrors?.map((item) => <p className={styles.error} key={item.surveyId}>{item.surveyId}: {item.error}</p>)}
        {job.catalogErrors?.map((item) => <p className={styles.error} key={item.surveyId}>{item.surveyId}: {item.error}</p>)}
        {job.spectrumErrors?.map((item) => <p className={styles.error} key={item.surveyId}>{item.surveyId}: {item.error}</p>)}
        {job.catalogs?.map((catalog) => (
          <section className={styles.catalog} key={catalog.surveyId}>
            <div><span>CATALOG EVIDENCE</span><strong>{catalog.surveyName} {catalog.release}</strong><small>{catalog.recordCount.toLocaleString()} sources in the bounded field</small></div>
            <dl>{Object.entries(catalog.summary).map(([key, value]) => <div key={key}><dt>{assetLabel(key)}</dt><dd>{value.toLocaleString()}</dd></div>)}</dl>
            <p>{catalog.caveats[0]} Screening counts are candidates for review, not automatic classifications.</p>
            <a href={catalog.catalogUrl}>Download the source catalog</a>
          </section>
        ))}
        {job.spectra?.map((spectrum) => (
          <section className={styles.spectrum} key={spectrum.surveyId}>
            <div><span>SPECTRUM EVIDENCE</span><strong>{spectrum.surveyName} {spectrum.release}</strong><small>{spectrum.instrument.toUpperCase()} · {spectrum.objectClass}{spectrum.objectSubclass ? ` / ${spectrum.objectSubclass}` : ""}</small></div>
            <Image src={spectrum.previewUrl} width={1200} height={450} unoptimized alt={`${spectrum.surveyName} spectrum found in Rubin tract ${tract}`} />
            <dl>
              <div><dt>Samples</dt><dd>{spectrum.sampleCount.toLocaleString()}</dd></div>
              <div><dt>Valid flux</dt><dd>{spectrum.validFluxSampleCount.toLocaleString()}</dd></div>
              <div><dt>Wavelength</dt><dd>{Math.round(spectrum.wavelengthRangeAngstrom[0]).toLocaleString()}–{Math.round(spectrum.wavelengthRangeAngstrom[1]).toLocaleString()} Å</dd></div>
              <div><dt>From tract center</dt><dd>{spectrum.separationArcmin.toFixed(2)}′</dd></div>
              <div><dt>Pipeline redshift</dt><dd>{spectrum.redshift == null ? "—" : spectrum.redshift.toFixed(6)}</dd></div>
              <div><dt>Warning flag</dt><dd>{spectrum.redshiftWarning ?? "—"}</dd></div>
            </dl>
            <p>{spectrum.caveats[0]} It is never rendered as an image layer or treated as an automatic Rubin-source association.</p>
            <div className={styles.downloads}><a href={spectrum.fitsUrl}>Download SDSS FITS</a><a href={spectrum.samplesUrl}>Download wavelength / flux CSV</a></div>
          </section>
        ))}
        {job.spectrumSearches?.map((search) => (
          <section className={styles.noResult} key={search.surveyId}>
            <span>BOUNDED SPECTRUM SEARCH</span><strong>{search.surveyName} {search.release}: no optical spectrum within {search.radiusArcmin.toFixed(0)}′</strong><p>{search.reason} This does not erase SDSS positional support elsewhere in the tract.</p>
          </section>
        ))}
        {job.layers?.some((layer) => Object.keys(layer.scienceAssets ?? {}).length > 0) && (
          <details className={styles.assets}>
            <summary>Download external science inputs</summary>
            {job.layers.map((layer) => Object.keys(layer.scienceAssets ?? {}).length > 0 && (
              <section key={layer.surveyId}>
                <strong>{layer.surveyName} · {layer.referenceBand}</strong>
                <ul>{Object.entries(layer.scienceAssets ?? {}).map(([key, url]) => <li key={key}><a href={url}>{assetLabel(key)}</a></li>)}</ul>
              </section>
            ))}
            <small>These are archive inputs and support planes. Their presence does not make a cross-survey subtraction comparison-ready.</small>
          </details>
        )}
        {(job.status === "not-requested" || job.status === "error") && <button type="button" disabled={busy} onClick={requestCutout}>{busy ? "Fetching Rubin pixels..." : job.status === "error" ? "Retry real Rubin pixels" : "Fetch real Rubin pixels"}</button>}
        {(job.status === "queued" || job.status === "processing") && <small>Science input status: {job.status}. This page checks for the validated result automatically.</small>}
        {workerStatus && <small>Background worker: {workerStatus.status} · checked {workerStatus.updatedAt.replace("T", " ").slice(0, 19)} UTC · {workerStatus.cadenceMinutes}-minute cadence.</small>}
      </div>
    </div>
  );
}

export default OnDemandRubinCutout;
