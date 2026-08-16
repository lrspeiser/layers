"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import styles from "./DifferenceExplorer.module.css";

export type DifferenceRegion = {
  regionId: string;
  tract: number;
  rank: number;
  maxAbsSigma: number | null;
  p99AbsSigma: number | null;
  fractionAbove5Sigma: number | null;
  peakCount: number;
  offSourcePeakCount: number;
  strongestOffSourceSigma: number | null;
  rubinBand: string | null;
  referenceBand: string | null;
  sameNamedBand: boolean;
};

export type DifferencePeak = {
  x: number;
  y: number;
  sigma: number;
  direction: "rubin-brighter" | "reference-brighter";
  onSource: boolean;
  rubinBrightnessPercentile: number | null;
  sky: { raDeg: number; decDeg: number };
};

type ViewMode = "overlay" | "rubin" | "reference" | "difference" | "blink";

export type ConfirmedPosition = {
  regionId: string;
  tract: number;
  sky: { raDeg: number; decDeg: number };
  seenIn: Record<string, number>;
  referenceCount: number;
  directionsAgree: boolean;
};

export type PairingIndex = {
  pairing: string;
  previewRoot: string;
  peakRoot: string;
  counts: Record<string, number>;
  regions: DifferenceRegion[];
};

const PAIRINGS: Array<{ id: string; label: string; file: string }> = [
  { id: "legacy", label: "Legacy DR10", file: "difference-index.json" },
  { id: "des", label: "DES DR2", file: "difference-index-des.json" },
  { id: "ps1", label: "Pan-STARRS", file: "difference-index-ps1.json" },
];

const MODES: Array<{ id: ViewMode; label: string; hint: string }> = [
  { id: "overlay", label: "Overlay", hint: "Difference laid over the Rubin frame" },
  { id: "blink", label: "Blink", hint: "Alternate Rubin and the reference" },
  { id: "rubin", label: "Rubin", hint: "Rubin frame alone" },
  { id: "reference", label: "Reference", hint: "Reference frame alone" },
  { id: "difference", label: "Difference", hint: "Difference alone, diverging about zero" },
];

export function DifferenceExplorer({
  regions,
  previewRoot,
  peakRoot,
  counts,
  caveat,
  peakClassification,
  confirmed = [],
  agreementCaveat,
}: {
  regions: DifferenceRegion[];
  previewRoot: string;
  peakRoot: string;
  counts: Record<string, number>;
  caveat: string;
  peakClassification: { onSource: string; offSource: string };
  confirmed?: ConfirmedPosition[];
  agreementCaveat?: string;
}) {
  // The other pairings are fetched when chosen rather than imported: three
  // indexes is 131 KB of route payload for something most visits never switch.
  const [pairing, setPairing] = useState("legacy");
  const [loaded, setLoaded] = useState<Record<string, PairingIndex>>({
    legacy: { pairing: "legacy", previewRoot, peakRoot, counts, regions },
  });
  const active = loaded[pairing];
  const [onlyOffSource, setOnlyOffSource] = useState(false);
  const [selectedId, setSelectedId] = useState(regions[0]?.regionId ?? "");
  const [mode, setMode] = useState<ViewMode>("overlay");
  const [opacity, setOpacity] = useState(0.75);
  const [peaks, setPeaks] = useState<DifferencePeak[]>([]);
  const [activePeak, setActivePeak] = useState<number | null>(null);
  const [blinkOnRubin, setBlinkOnRubin] = useState(true);
  const [loadingPeaks, setLoadingPeaks] = useState(false);

  useEffect(() => {
    if (loaded[pairing]) return;
    const entry = PAIRINGS.find((p) => p.id === pairing);
    if (!entry) return;
    let cancelled = false;
    fetch(`/data/layers/selected-regions/${entry.file}`)
      .then((response) => (response.ok ? response.json() : Promise.reject(response.status)))
      .then((data: PairingIndex) => {
        if (!cancelled) setLoaded((current) => ({ ...current, [pairing]: data }));
      })
      .catch(() => {
        if (!cancelled) setPairing("legacy");
      });
    return () => {
      cancelled = true;
    };
  }, [pairing, loaded]);

  const activeRegions = active?.regions ?? [];
  const visible = useMemo(
    () => (onlyOffSource ? activeRegions.filter((r) => r.offSourcePeakCount > 0) : activeRegions),
    [activeRegions, onlyOffSource],
  );
  const confirmedTracts = useMemo(
    () => new Set(confirmed.map((item) => item.regionId)),
    [confirmed],
  );

  // Keep the selection inside the filtered list, or the viewer shows a region
  // the list no longer offers and the two panels disagree.
  useEffect(() => {
    if (visible.length && !visible.some((r) => r.regionId === selectedId)) {
      setSelectedId(visible[0].regionId);
    }
  }, [visible, selectedId]);

  const selected = useMemo(
    () => activeRegions.find((r) => r.regionId === selectedId) ?? null,
    [activeRegions, selectedId],
  );

  // Peaks are fetched per region rather than bundled: all of them together are
  // 0.9 MB, and importing a file that size into a route broke every tract page
  // once before.
  useEffect(() => {
    if (!selectedId) return;
    let cancelled = false;
    setLoadingPeaks(true);
    setActivePeak(null);
    if (!active) return;
    fetch(`${active.peakRoot}/${selectedId}.json`)
      .then((response) => (response.ok ? response.json() : Promise.reject(response.status)))
      .then((data) => {
        if (!cancelled) setPeaks(data.peaks ?? []);
      })
      .catch(() => {
        if (!cancelled) setPeaks([]);
      })
      .finally(() => {
        if (!cancelled) setLoadingPeaks(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedId, active]);

  const blinkTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  useEffect(() => {
    if (mode !== "blink") {
      if (blinkTimer.current) clearInterval(blinkTimer.current);
      blinkTimer.current = null;
      setBlinkOnRubin(true);
      return;
    }
    blinkTimer.current = setInterval(() => setBlinkOnRubin((value) => !value), 750);
    return () => {
      if (blinkTimer.current) clearInterval(blinkTimer.current);
    };
  }, [mode]);

  const src = useCallback(
    (name: string) => `${active?.previewRoot ?? previewRoot}/${selectedId}/${name}.png`,
    [active, previewRoot, selectedId],
  );

  // The band is part of the filename and is not always r: a few regions compare
  // Rubin r against a reference in g, i or z. Assuming r pointed at files that
  // do not exist, which renders as an empty frame with no error.
  const rubinImage = selected?.rubinBand ? src(`rubin-${selected.rubinBand}`) : src("rubin-r");
  const referenceImage = selected?.referenceBand
    ? src(`reference-${selected.referenceBand}`)
    : src("reference-r");

  const baseImage =
    mode === "reference" || (mode === "blink" && !blinkOnRubin)
      ? referenceImage
      : mode === "difference"
        ? src("difference")
        : rubinImage;

  const shownPeaks = peaks.filter((peak) => !onlyOffSource || !peak.onSource);

  return (
    <section className={styles.explorer}>
      <header className={styles.header}>
        <div>
          <span>WHERE THE SURVEYS DISAGREE</span>
          <h1>Difference explorer</h1>
          <p>
            Every reconciled region carries a difference plane. This draws it, scaled by the
            difference&rsquo;s own measured scatter, so you can see where Rubin and the reference
            disagree instead of comparing two frames by eye. Regions are ranked by how much of the
            frame disagrees, not by the single hottest pixel.
          </p>
        </div>
        <dl className={styles.counts}>
          <div>
            <dt>regions</dt>
            <dd>{counts.regionsRendered}</dd>
          </div>
          <div>
            <dt>peaks</dt>
            <dd>{counts.totalPeaks}</dd>
          </div>
          <div>
            <dt>off-source</dt>
            <dd>{counts.offSourcePeaks}</dd>
          </div>
        </dl>
      </header>

      <p className={styles.caveat} role="note">
        <strong>A bright patch here is a disagreement, not a discovery.</strong> {caveat}
      </p>

      {confirmed.length > 0 && (
        <div className={styles.confirmed}>
          <h2>Seen against more than one reference</h2>
          <p>
            {confirmed.length} position{confirmed.length === 1 ? "" : "s"} of{" "}
            {counts.distinctOffSourcePositions ?? "—"} off-source ones appear against two
            independent references. Rubin is the only term those comparisons share, so a repeated
            peak sits on the Rubin side and a solitary one belongs to the reference that shows it.
          </p>
          <ul>
            {confirmed.map((item) => (
              <li key={`${item.regionId}-${item.sky.raDeg}`}>
                <button
                  type="button"
                  onClick={() => {
                    const owner = Object.keys(item.seenIn)[0];
                    if (owner && owner !== pairing) setPairing(owner);
                    setSelectedId(item.regionId);
                  }}
                >
                  <strong>Tract {item.tract}</strong>
                  <span>
                    {item.sky.raDeg.toFixed(4)}, {item.sky.decDeg.toFixed(4)}
                  </span>
                  <em>
                    {Object.entries(item.seenIn)
                      .map(([name, value]) => `${name} ${Math.abs(value).toFixed(0)}σ`)
                      .join(" · ")}
                  </em>
                </button>
              </li>
            ))}
          </ul>
          {agreementCaveat && <p className={styles.muted}>{agreementCaveat}</p>}
        </div>
      )}

      <div className={styles.pairings} role="group" aria-label="Reference survey">
        <span>Compare Rubin against</span>
        {PAIRINGS.map((item) => (
          <button
            key={item.id}
            type="button"
            data-active={pairing === item.id}
            onClick={() => setPairing(item.id)}
          >
            {item.label}
          </button>
        ))}
        {!active && <em>loading…</em>}
      </div>

      <div className={styles.body}>
        <aside className={styles.list}>
          <label className={styles.filter}>
            <input
              type="checkbox"
              checked={onlyOffSource}
              onChange={(event) => setOnlyOffSource(event.target.checked)}
            />
            <span>
              Only regions with an off-source peak
              <small>
                {counts.regionsWithAnOffSourcePeak} of {counts.regionsRendered}. {peakClassification.onSource}
              </small>
            </span>
          </label>
          <ol>
            {visible.map((region) => (
              <li key={region.regionId}>
                <button
                  type="button"
                  data-active={region.regionId === selectedId}
                  onClick={() => setSelectedId(region.regionId)}
                >
                  <strong>
                    Tract {region.tract}
                    {!region.sameNamedBand && (
                      <em className={styles.crossBandTag}>
                        {region.rubinBand}/{region.referenceBand}
                      </em>
                    )}
                  </strong>
                  <span className={styles.metric}>
                    {((region.fractionAbove5Sigma ?? 0) * 100).toFixed(2)}% of pixels above 5&sigma;
                  </span>
                  <span className={styles.tags}>
                    {region.offSourcePeakCount > 0 && (
                      <em className={styles.badge}>{region.offSourcePeakCount} off-source</em>
                    )}
                    {confirmedTracts.has(region.regionId) && (
                      <em className={styles.confirmedBadge}>confirmed</em>
                    )}
                  </span>
                </button>
              </li>
            ))}
          </ol>
        </aside>

        <div className={styles.viewer}>
          <div className={styles.modes} role="group" aria-label="View mode">
            {MODES.map((item) => (
              <button
                key={item.id}
                type="button"
                title={item.hint}
                data-active={mode === item.id}
                onClick={() => setMode(item.id)}
              >
                {item.label}
              </button>
            ))}
          </div>

          {selectedId && (
            <figure className={styles.frame}>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={baseImage} alt={`${selectedId} ${mode}`} width={512} height={512} />
              {mode === "overlay" && (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  className={styles.overlay}
                  style={{ opacity }}
                  src={src("difference-overlay")}
                  alt="difference overlay"
                  width={512}
                  height={512}
                />
              )}
              {shownPeaks.map((peak, index) => (
                <button
                  key={`${peak.x}-${peak.y}-${index}`}
                  type="button"
                  className={styles.marker}
                  data-off-source={!peak.onSource}
                  data-active={activePeak === index}
                  style={{ left: `${peak.x * 100}%`, top: `${(1 - peak.y) * 100}%` }}
                  onClick={() => setActivePeak(activePeak === index ? null : index)}
                  aria-label={`peak ${peak.sigma} sigma`}
                />
              ))}
              <figcaption>
                {mode === "blink"
                  ? blinkOnRubin
                    ? "Rubin"
                    : "Reference"
                  : MODES.find((m) => m.id === mode)?.hint}
              </figcaption>
            </figure>
          )}

          {mode === "overlay" && (
            <label className={styles.slider}>
              Overlay opacity
              <input
                type="range"
                min={0}
                max={1}
                step={0.05}
                value={opacity}
                onChange={(event) => setOpacity(Number(event.target.value))}
              />
              <span>{Math.round(opacity * 100)}%</span>
            </label>
          )}

          <div className={styles.key}>
            <span data-swatch="red" /> Rubin brighter
            <span data-swatch="blue" /> reference brighter
            <span data-swatch="ring" /> off-source peak
          </div>
        </div>

        <aside className={styles.detail}>
          {selected && (
            <>
              <h2>Tract {selected.tract}</h2>
              {!selected.sameNamedBand && (
                <p className={styles.crossBand} role="note">
                  <strong>
                    Rubin {selected.rubinBand} against reference {selected.referenceBand}.
                  </strong>{" "}
                  Different filters, so these frames disagree everywhere for a reason that has
                  nothing to do with the sky. Read this one for method, not for anomalies.
                </p>
              )}
              <dl>
                <div>
                  <dt>rank by disagreeing area</dt>
                  <dd>
                    {selected.rank} of {counts.regionsRendered}
                  </dd>
                </div>
                <div>
                  <dt>pixels above 5&sigma;</dt>
                  <dd>{((selected.fractionAbove5Sigma ?? 0) * 100).toFixed(2)}%</dd>
                </div>
                <div>
                  <dt>99th percentile</dt>
                  <dd>{selected.p99AbsSigma?.toFixed(1)}&sigma;</dd>
                </div>
                <div>
                  <dt>peaks</dt>
                  <dd>
                    {selected.peakCount} ({selected.offSourcePeakCount} off-source)
                  </dd>
                </div>
              </dl>
              {loadingPeaks && <p className={styles.muted}>loading peaks…</p>}
              {activePeak !== null && shownPeaks[activePeak] && (
                <div className={styles.peakCard}>
                  <strong>
                    {Math.abs(shownPeaks[activePeak].sigma).toFixed(1)}&sigma;{" "}
                    {shownPeaks[activePeak].direction === "rubin-brighter"
                      ? "Rubin brighter"
                      : "reference brighter"}
                  </strong>
                  <p>
                    {shownPeaks[activePeak].sky.raDeg.toFixed(5)},{" "}
                    {shownPeaks[activePeak].sky.decDeg.toFixed(5)}
                  </p>
                  <p className={styles.muted}>
                    {shownPeaks[activePeak].onSource
                      ? peakClassification.onSource
                      : peakClassification.offSource}
                  </p>
                </div>
              )}
              {activePeak === null && !loadingPeaks && shownPeaks.length > 0 && (
                <p className={styles.muted}>
                  {shownPeaks.length} marker{shownPeaks.length === 1 ? "" : "s"} on the frame. Click
                  one for its position and reading.
                </p>
              )}
              <a className={styles.link} href={`/overlay/${selected.tract}`}>
                Open the full multi-survey overlay →
              </a>
            </>
          )}
        </aside>
      </div>
    </section>
  );
}

export default DifferenceExplorer;
