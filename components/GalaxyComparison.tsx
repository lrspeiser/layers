"use client";

import { useEffect, useMemo, useState } from "react";
import type { Galaxy } from "@/lib/galaxies";
import type { AtlasManifest, MetricClassification } from "@/lib/atlas-manifest";
import { manifestUrl } from "@/lib/atlas-manifest";

const allBands = ["RGB", "u", "g", "r", "i", "z", "y", "Diffuse"] as const;
type Band = (typeof allBands)[number];
type LoadState = "loading" | "missing" | "invalid" | "ready";

const signalLabels: Record<MetricClassification, string> = {
  large: "Large difference",
  above: "Above expected",
  expected: "Within expected",
};

function imageForBand(manifest: AtlasManifest, side: "rubin" | "legacy", band: Band) {
  if (band === "RGB") return manifest.images[side].rgb;
  if (band === "Diffuse") {
    return side === "rubin" ? manifest.images.rubin.diffuse ?? manifest.images.rubin.rgb : manifest.images.legacy.rgb;
  }
  return manifest.images[side].bands?.[band] ?? manifest.images[side].rgb;
}

export function GalaxyComparison({ galaxy, record = false }: { galaxy: Galaxy; record?: boolean }) {
  const [band, setBand] = useState<Band>("RGB");
  const [reveal, setReveal] = useState(52);
  const [manifest, setManifest] = useState<AtlasManifest | null>(null);
  const [loadState, setLoadState] = useState<LoadState>("loading");

  useEffect(() => {
    const controller = new AbortController();
    setLoadState("loading");
    setManifest(null);
    setBand("RGB");
    setReveal(52);

    fetch(manifestUrl(galaxy.slug), { signal: controller.signal })
      .then(async (response) => {
        if (response.status === 404) return null;
        if (!response.ok) throw new Error(`Manifest request failed: ${response.status}`);
        return (await response.json()) as AtlasManifest;
      })
      .then((nextManifest) => {
        if (!nextManifest) {
          setLoadState("missing");
          return;
        }
        const usable =
          nextManifest.objectId === galaxy.slug &&
          nextManifest.release === "EDP2" &&
          nextManifest.verified === true &&
          Boolean(nextManifest.images?.rubin?.rgb) &&
          Boolean(nextManifest.images?.legacy?.rgb);
        setManifest(nextManifest);
        setLoadState(usable ? "ready" : "invalid");
      })
      .catch((error: unknown) => {
        if ((error as Error).name !== "AbortError") setLoadState("invalid");
      });

    return () => controller.abort();
  }, [galaxy.slug]);

  const availableBands = useMemo(() => {
    if (!manifest || loadState !== "ready") return allBands;
    return allBands.filter((item) => {
      if (item === "RGB") return true;
      if (item === "Diffuse") return Boolean(manifest.images.rubin.diffuse);
      return Boolean(manifest.images.rubin.bands[item]);
    });
  }, [loadState, manifest]);

  const comparison = manifest && loadState === "ready" ? (
    <>
      <div className={`comparison-view ${record ? "record-comparison" : ""} band-${band.toLowerCase()}`}>
        <div className="comparison-layer rubin-layer">
          <img src={imageForBand(manifest, "rubin", band)} alt={`${galaxy.name} Rubin EDP2 ${band} view`} />
        </div>
        <div className="comparison-layer legacy-layer" style={{ clipPath: `inset(0 ${100 - reveal}% 0 0)` }} aria-hidden="true">
          <img src={imageForBand(manifest, "legacy", band)} alt="" />
          <span>LEGACY</span>
        </div>
        <span className="rubin-label">RUBIN EDP2 · {band}</span>
        <div className="slider-line" style={{ left: `${reveal}%` }}><i>↔</i></div>
        <input
          className="comparison-range"
          type="range"
          min="8"
          max="92"
          value={reveal}
          onChange={(event) => setReveal(Number(event.target.value))}
          aria-label={`Reveal ${galaxy.name} legacy comparison`}
        />
      </div>
      <div className={`viewer-caption ${record ? "record-viewer-caption" : ""}`}>
        <span>{manifest.field.widthArcmin}′ field · {manifest.field.pixelScaleArcsec}″ px</span>
        <span className="alignment-status"><i /> VERIFIED · WCS + PSF + SKY MATCHED</span>
        <span>Residual ≤ {manifest.registration.maxResidualArcsec.toFixed(2)}″</span>
      </div>
    </>
  ) : (
    <div className={`ingest-gate ${galaxy.legacyPreview ? "legacy-ready-gate" : ""} ${record ? "record-ingest-gate" : ""}`}>
      {galaxy.legacyPreview && <img className="legacy-ready-preview" src={galaxy.legacyPreview} alt={`${galaxy.name} real Spitzer IRAC channel 1 archive cutout`} />}
      <div className="coordinate-reticle" aria-hidden="true"><i /><b /></div>
      <div className="ingest-copy">
        <span className={`ingest-state state-${loadState}`}>{loadState === "loading" ? "Checking Rubin manifest" : loadState === "invalid" ? "Rubin manifest blocked" : galaxy.legacyPreview ? "SPARC + Spitzer ready · Rubin pending" : "SPARC ready · Rubin + Spitzer pending"}</span>
        <h4>{galaxy.legacyPreview ? "Real legacy pixels are ready." : "No substitute image shown."}</h4>
        <p>
          {galaxy.legacyPreview
            ? "This is the target’s real public Spitzer/IRAC 3.6 μm cutout. The slider stays locked because this target is outside the three Rubin fields currently ingested into Layers."
            : "The SPARC profile is loaded, but no Spitzer SEIP image covers this target. A different named legacy survey and a usable Rubin field are required."}
        </p>
        <div className="target-coordinates">
          <span><small>RA</small>{galaxy.raDeg.toFixed(6)}°</span>
          <span><small>DEC</small>{galaxy.decDeg.toFixed(6)}°</span>
          <span><small>FIELD</small>{galaxy.fieldWidthArcmin}′</span>
          <span><small>SPARC RMAX</small>{galaxy.sparcProfileMaxArcsec.toFixed(1)}″</span>
        </div>
      </div>
      <div className="ingest-contract" aria-label="Required ingest checks">
        <span className="gate-done"><i>✓</i> SPARC profile</span>
        <span className={galaxy.legacyPreview ? "gate-done" : ""}><i>{galaxy.legacyPreview ? "✓" : "2"}</i> Spitzer IRAC1</span>
        <span><i>3</i> Rubin EDP2</span>
        <span><i>4</i> Registration QA</span>
      </div>
    </div>
  );

  const metrics = manifest?.metrics?.length ? manifest.metrics : null;

  return (
    <div className={record ? "record-comparison-column" : "comparison-stack"}>
      <div className={`viewer-tabs ${record ? "record-viewer-tabs" : ""}`} role="group" aria-label={`${galaxy.name} image band`}>
        {availableBands.map((item) => (
          <button key={item} className={band === item ? "active" : ""} disabled={loadState !== "ready"} onClick={() => setBand(item)}>{item}</button>
        ))}
        <span className={`viewer-verification verify-${loadState}`}>{loadState === "ready" ? "Verified pixels" : "No published pixels"}</span>
      </div>
      {comparison}
      {!record && (
        <>
          <div className="metrics-row">
            {(metrics ?? [
              { label: "Outer radius", value: "Rubin pending", uncertainty: `SPARC profile accepted to ${galaxy.sparcProfileMaxArcsec.toFixed(1)}″`, expectedRange: "New optical extent not measured" },
              { label: "Δgbar", value: "Not measured", uncertainty: "Needs mass-to-light model", expectedRange: "No illustrative value shown" },
              { label: "Inclination revision", value: "Not measured", uncertainty: "Needs matched isophote fit", expectedRange: "Compared with fit uncertainty" },
              { label: "Faint structures", value: "Not reviewed", uncertainty: "Needs injection–recovery test", expectedRange: "False-positive rate required" },
            ]).map((metric) => {
              const measured = "classification" in metric;
              return (
                <div className="metric-tile" key={metric.label}>
                  <span className={`signal-badge signal-${measured ? metric.classification : "pending"}`}>{measured ? signalLabels[metric.classification] : "Pending verified data"}</span>
                  <span>{metric.label}</span>
                  <strong>{metric.value}</strong>
                  <small>{metric.uncertainty}</small>
                  <small className="metric-benchmark">{measured ? `${metric.significanceSigma.toFixed(1)}σ · expected ${metric.expectedRange}` : metric.expectedRange}</small>
                </div>
              );
            })}
          </div>
          <div className="metric-key">
            <strong>Expected or big?</strong>
            <span><i className="key-large" /> Large: ≥3σ beyond the measured comparison baseline</span>
            <span><i className="key-above" /> Above expected: 2–3σ</span>
            <span><i className="key-expected" /> Within expected: &lt;2σ</span>
          </div>
        </>
      )}
    </div>
  );
}
