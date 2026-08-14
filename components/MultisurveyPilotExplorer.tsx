"use client";

import { useMemo, useState } from "react";
import styles from "./MultisurveyPilotExplorer.module.css";

export type PilotDataset = {
  dataset: string;
  label: string;
  status: "available" | "none" | "error";
  coverage: boolean;
  release: string;
  summary: string;
  readiness: string;
  caveats: string[];
};

export type MultisurveyPilot = {
  id: string;
  name: string;
  raDeg: number;
  decDeg: number;
  alignedRubinPreview: string;
  alignedLotssPreview: string;
  coveragePreview: string;
  overlayPreview: string;
  commonCoverageFraction: number;
  datasets: PilotDataset[];
};

export function MultisurveyPilotExplorer({ pilots, initialPilotId }: { pilots: MultisurveyPilot[]; initialPilotId?: string }) {
  const [activeId, setActiveId] = useState(() => pilots.some((pilot) => pilot.id === initialPilotId) ? initialPilotId ?? "" : pilots[0]?.id ?? "");
  const [viewMode, setViewMode] = useState<"swipe" | "coverage" | "overlay">("swipe");
  const [reveal, setReveal] = useState(50);
  const active = useMemo(() => pilots.find((pilot) => pilot.id === activeId) ?? pilots[0], [activeId, pilots]);

  if (!active) return null;

  return (
    <section className={styles.explorer} aria-labelledby="pilot-explorer-title">
      <header>
        <div>
          <span>LIVE MULTI-SURVEY PILOTS</span>
          <h1 id="pilot-explorer-title">The same sky, in genuinely different data</h1>
          <p>These are live archive results at the three Rubin pilot positions—not decorative examples. Switch layers, then inspect what each dataset can and cannot support.</p>
        </div>
        <a href="/prototype">Open aligned optical prototype →</a>
      </header>

      <div className={styles.brief}>
        <article><span>GOAL</span><p>Find information Rubin adds—or assumptions another wavelength can overturn—at the same celestial coordinates.</p></article>
        <article><span>DETAILS</span><p>Gaia catalogs, ZTF time series, eROSITA coverage, VLASS metadata, and authentic LoTSS radio FITS were queried live.</p></article>
        <article><span>ISSUES</span><p>Cross-wavelength brightness is not subtraction-ready. Registration, beam/PSF, masks, units, and selection functions remain hard gates.</p></article>
      </div>

      <nav className={styles.fieldTabs} aria-label="Pilot field">
        {pilots.map((pilot) => (
          <button key={pilot.id} type="button" aria-pressed={pilot.id === active.id} onClick={() => setActiveId(pilot.id)}>{pilot.name}</button>
        ))}
      </nav>

      <div className={styles.workspace}>
        <div className={styles.viewer}>
          <div className={styles.viewerTop}>
            <div><span>FIELD</span><strong>{active.name}</strong><small>RA {active.raDeg.toFixed(6)}° · Dec {active.decDeg.toFixed(6)}°</small></div>
            <div className={styles.layerToggle} role="group" aria-label="Visible data layer">
              <button type="button" aria-pressed={viewMode === "swipe"} onClick={() => setViewMode("swipe")}>Swipe</button>
              <button type="button" aria-pressed={viewMode === "coverage"} onClick={() => setViewMode("coverage")}>Common coverage</button>
              <button type="button" aria-pressed={viewMode === "overlay"} onClick={() => setViewMode("overlay")}>Position overlay</button>
            </div>
          </div>
          {viewMode === "swipe" ? (
            <div className={styles.imageStage}>
              {/* Exact precomputed common-grid displays; quantitative work uses the retained FITS. */}
              <img src={active.alignedRubinPreview} alt={`${active.name} Rubin DP2 i-band on the LoTSS grid`} />
              <div className={styles.revealLayer} style={{ clipPath: `inset(0 ${100 - reveal}% 0 0)` }} aria-hidden="true"><img src={active.alignedLotssPreview} alt="" /></div>
              <span className={styles.leftLabel}>LOTSS DR3 · 144 MHz</span><span className={styles.rightLabel}>RUBIN DP2 · i BAND</span>
              <div className={styles.sliderLine} style={{ left: `${reveal}%` }}><i>↔</i></div>
              <input className={styles.range} type="range" min="3" max="97" value={reveal} onChange={(event) => setReveal(Number(event.target.value))} aria-label={`Reveal ${active.name} LoTSS radio over Rubin optical`} />
            </div>
          ) : (
            <div className={styles.imageStage}>
              <img src={viewMode === "coverage" ? active.coveragePreview : active.overlayPreview} alt={`${active.name} ${viewMode === "coverage" ? "common finite-pixel coverage mask" : "Rubin and LoTSS positional overlay"}`} />
              <span className={styles.leftLabel}>{viewMode === "coverage" ? "WHITE = BOTH DATASETS HAVE FINITE PIXELS" : "ORANGE / CYAN POSITIONAL OVERLAY"}</span>
            </div>
          )}
          <div className={styles.gridFacts}><span><strong>{(active.commonCoverageFraction * 100).toFixed(2)}%</strong> common finite coverage</span><span><strong>200 × 200</strong> shared LoTSS grid</span><span><strong>nJy / Jy beam⁻¹</strong> incompatible flux units</span></div>
          <p className={styles.viewerWarning}><strong>Astrometrically aligned, not photometrically comparable.</strong> Rubin was bilinearly reprojected for positional display only. No PSF/beam, bandpass, pixel-area, or noise matching has been performed, so this is not a subtraction or missing-light map.</p>
        </div>

        <aside className={styles.evidence}>
          <div className={styles.evidenceHeading}><span>EVIDENCE AT THIS POSITION</span><strong>{active.datasets.filter((item) => item.status === "available").length} / {active.datasets.length} datasets returned usable records</strong></div>
          {active.datasets.map((dataset) => (
            <article key={dataset.dataset}>
              <div><strong>{dataset.label}</strong><em data-status={dataset.status}>{dataset.status === "available" ? "AVAILABLE" : dataset.status === "none" ? "NO PUBLIC COVERAGE" : "QUERY ERROR"}</em></div>
              <small>{dataset.release}</small>
              <p>{dataset.summary}</p>
              <details><summary>Readiness and caveats</summary><p>{dataset.readiness}</p>{dataset.caveats.map((item) => <p key={item}>{item}</p>)}</details>
            </article>
          ))}
        </aside>
      </div>
    </section>
  );
}

export default MultisurveyPilotExplorer;
