"use client";

import { useMemo, useState, type CSSProperties } from "react";
import styles from "./CoverageExplorer.module.css";

export type CoverageAvailability =
  | "pixels-ready"
  | "catalog-ready"
  | "metadata-only"
  | "no-overlap"
  | "planned"
  | "unavailable";

export type DatasetCategory =
  | "optical"
  | "uv-ir"
  | "astrometry"
  | "time-domain"
  | "spectroscopy"
  | "x-ray"
  | "radio"
  | "gas"
  | "high-resolution"
  | "lensing"
  | "microwave"
  | (string & {});

export interface SurveyRegistryEntry {
  id: string;
  name: string;
  shortName?: string;
  category: DatasetCategory;
  availability: CoverageAvailability;
  /** Human-readable release identifier, not inferred by this component. */
  release?: string;
  /** Number of Rubin tracts with a confirmed footprint intersection. */
  overlappingTractCount: number;
  /** Number of those tracts whose science pixels are cached by this product. */
  pixelCachedTractCount: number;
  /** Exact released-detection or named-product intersections that do not represent the full parent survey. */
  subsetTractCount?: number;
  subsetLabel?: string;
  /** How the registry established the footprint intersections. */
  coverageEvidence?: "moc" | "archive-query" | "moc-and-archive-query";
  coverageUpdatedAt?: string;
  description?: string;
  sourceUrl?: string;
  exploreUrl?: string;
}

export interface RubinTractCoverage {
  id: string;
  raDeg: number;
  decDeg: number;
  bands?: string[];
  /** Survey IDs with an exact MOC or archive-query footprint intersection. */
  overlapSurveyIds: string[];
  /** Survey IDs with exact released-detection or named-product subset intersections. */
  subsetSurveyIds?: string[];
  /** Survey IDs for which science pixels have been fetched and cached by the product. */
  pixelCachedSurveyIds?: string[];
  /** Number of automatically selected acquisition candidates in this tract. */
  selectedRegionCount?: number;
  /** Optional label supplied by the parent, such as a field or tract group. */
  label?: string;
  /** Optional app route for a tract workspace; keeps this prop boundary serializable. */
  href?: string;
  previewImage?: string;
  layerThumbnails?: { surveyName: string; band: string; image: string }[];
  /** Distinguishes an aligned viewer, a validated single-survey image, and evidence only. */
  viewerStatus?: "aligned-viewer" | "image-only" | "evidence-only";
}

export interface RubinCoverageSummary {
  release: string;
  tracts: RubinTractCoverage[];
  /** Authoritative release total; may exceed loaded tracts during indexing. */
  totalTractCount: number;
  indexedTractCount: number;
  footprintAreaSqDeg?: number;
  coverageUpdatedAt?: string;
  selectedRegionCount: number;
  selectedRegionGoal?: number;
  acquisition?: {
    plannedJobCount: number;
    metadataResponseCount: number;
    cachedScienceInputCandidateCount: number;
    validatedScienceInputCount: number;
    comparisonReadyCount: number;
    honestStatus: string;
  };
}

export interface CoverageExplorerProps {
  coverage: RubinCoverageSummary;
  surveys: SurveyRegistryEntry[];
  title?: string;
  /** Parent-controlled selection ID, safe to pass through a server/client boundary. */
  selectedTractId?: string;
  className?: string;
  objectiveAudit?: {
    objectiveAchieved: boolean;
    pixelFamilyRegionCounts: Record<string, number>;
    unresolvedCoverageSurveyIds: string[];
  };
}

type ScopeFilter = "all" | "overlap" | "pixels" | "no-overlap";

const AVAILABILITY_LABELS: Record<CoverageAvailability, string> = {
  "pixels-ready": "Science pixels ready",
  "catalog-ready": "Catalog ready",
  "metadata-only": "Coverage only",
  "no-overlap": "No Rubin overlap",
  planned: "Planned",
  unavailable: "Unavailable",
};

function categoryLabel(category: string) {
  return category
    .split("-")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function displayDate(value?: string) {
  if (!value) return undefined;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat("en-US", { dateStyle: "medium", timeZone: "UTC" }).format(date);
}

const countFormatter = new Intl.NumberFormat("en-US");
function formatCount(value: number) { return countFormatter.format(value); }

export function CoverageExplorer({
  coverage,
  surveys,
  title = "Rubin footprint coverage",
  selectedTractId,
  className,
  objectiveAudit,
}: CoverageExplorerProps) {
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState<string>("all");
  const [scope, setScope] = useState<ScopeFilter>("all");
  const [activeTractId, setActiveTractId] = useState<string | undefined>(selectedTractId);

  const surveyById = useMemo(() => new Map(surveys.map((survey) => [survey.id, survey])), [surveys]);
  const categories = useMemo(
    () => Array.from(new Set(surveys.map((survey) => survey.category))).sort(),
    [surveys],
  );

  const categorySurveyIds = useMemo(() => {
    if (category === "all") return undefined;
    return new Set(surveys.filter((survey) => survey.category === category).map((survey) => survey.id));
  }, [category, surveys]);

  const filteredTracts = useMemo(() => {
    const normalizedQuery = query.trim().toLocaleLowerCase();
    return coverage.tracts.filter((tract) => {
      const tractOverlapIds = [...tract.overlapSurveyIds, ...(tract.subsetSurveyIds ?? [])];
      const relevantOverlaps = categorySurveyIds
        ? tractOverlapIds.filter((id) => categorySurveyIds.has(id))
        : tractOverlapIds;
      const relevantPixels = categorySurveyIds
        ? (tract.pixelCachedSurveyIds ?? []).filter((id) => categorySurveyIds.has(id))
        : (tract.pixelCachedSurveyIds ?? []);
      const surveyNames = tractOverlapIds
        .map((id) => {
          const survey = surveyById.get(id);
          return survey ? `${survey.name} ${survey.shortName ?? ""} ${survey.id}` : id;
        })
        .join(" ")
        .toLocaleLowerCase();
      const matchesQuery = !normalizedQuery
        || tract.id.toLocaleLowerCase().includes(normalizedQuery)
        || tract.label?.toLocaleLowerCase().includes(normalizedQuery)
        || surveyNames.includes(normalizedQuery);
      const matchesScope = scope === "all"
        || (scope === "overlap" && relevantOverlaps.length > 0)
        || (scope === "pixels" && relevantPixels.length > 0)
        || (scope === "no-overlap" && relevantOverlaps.length === 0);
      const matchesCategory = !categorySurveyIds || scope === "no-overlap" || relevantOverlaps.length > 0;
      return matchesQuery && matchesScope && matchesCategory;
    });
  }, [categorySurveyIds, coverage.tracts, query, scope, surveyById]);

  const activeTract = coverage.tracts.find((tract) => tract.id === (activeTractId ?? selectedTractId));
  const selectedRegionGoal = coverage.selectedRegionGoal ?? 50;
  const goalPercent = Math.min(100, Math.round(coverage.selectedRegionCount / Math.max(selectedRegionGoal, 1) * 100));
  const indexingPercent = Math.min(100, Math.round(coverage.indexedTractCount / Math.max(coverage.totalTractCount, 1) * 100));
  const pixelReadySurveyCount = surveys.filter((survey) => survey.pixelCachedTractCount > 0).length;

  function selectTract(tract: RubinTractCoverage) {
    setActiveTractId(tract.id);
  }

  function inspectSurvey(survey: SurveyRegistryEntry) {
    setCategory("all");
    setScope("overlap");
    setQuery(survey.shortName ?? survey.name);
    requestAnimationFrame(() => document.getElementById("coverage-controls")?.scrollIntoView({ behavior: "smooth", block: "start" }));
  }

  return (
    <section className={`${styles.explorer}${className ? ` ${className}` : ""}`} aria-labelledby="coverage-explorer-title">
      <header className={styles.header}>
        <div>
          <span className={styles.eyebrow}>TRACT-FIRST DATA INDEX</span>
          <h2 id="coverage-explorer-title">{title}</h2>
          <p>
            Start with every indexed Rubin tract, then inspect confirmed footprint intersections and the smaller set
            with validated pixels or honest display alignments. Quantitative comparison readiness is tracked separately.
          </p>
        </div>
        <div className={styles.release} aria-label="Rubin release details">
          <span>RUBIN RELEASE</span>
          <strong>{coverage.release}</strong>
          {coverage.coverageUpdatedAt && <small>Coverage checked {displayDate(coverage.coverageUpdatedAt)}</small>}
        </div>
      </header>

      <div className={styles.metrics} aria-label="Coverage summary">
        <article>
          <span>INDEXED TRACTS</span>
          <strong>{formatCount(coverage.indexedTractCount)} <small>/ {formatCount(coverage.totalTractCount)}</small></strong>
          <div className={styles.progressTrack} aria-label={`${indexingPercent}% of Rubin tracts indexed`}>
            <i style={{ width: `${indexingPercent}%` }} />
          </div>
        </article>
        <article>
          <span>EXTERNAL DATASETS</span>
          <strong>{formatCount(surveys.length)} <small>registered</small></strong>
          <p>{pixelReadySurveyCount} cached in at least one tract</p>
        </article>
        <article>
          <span>SELECTED ACQUISITION REGIONS</span>
          <strong>{coverage.selectedRegionCount} <small>/ {selectedRegionGoal}</small></strong>
          <div className={styles.progressTrack} aria-label={`${goalPercent}% of the region goal complete`}>
            <i style={{ width: `${goalPercent}%` }} />
          </div>
          <p>coverage-ranked; pixels are tracked separately</p>
        </article>
        <article>
          <span>FOOTPRINT AREA</span>
          <strong>{coverage.footprintAreaSqDeg === undefined ? "Not supplied" : formatCount(coverage.footprintAreaSqDeg)}</strong>
          {coverage.footprintAreaSqDeg !== undefined && <p>square degrees reported by the index</p>}
        </article>
      </div>

      <div className={styles.truthBanner} role="note">
        <strong>Coverage is not a science comparison.</strong>
        <span>An exact overlap comes from a MOC or archive position query. “Pixels cached” means data have also been fetched by this product; neither state proves that images are aligned, equivalent, or evidence of a difference.</span>
      </div>

      {coverage.acquisition && (
        <section className={styles.acquisition} aria-labelledby="acquisition-status-title">
          <div>
            <span>ACQUISITION PIPELINE STATUS</span>
            <h3 id="acquisition-status-title">What is planned, fetched, and scientifically usable</h3>
            <p>{coverage.acquisition.honestStatus}</p>
          </div>
          <dl>
            <div><dt>Planned jobs</dt><dd>{formatCount(coverage.acquisition.plannedJobCount)}</dd></div>
            <div><dt>Metadata responses cached</dt><dd>{coverage.acquisition.metadataResponseCount}</dd></div>
            <div><dt>Raw science candidates</dt><dd>{coverage.acquisition.cachedScienceInputCandidateCount}</dd></div>
            <div><dt>Validated science inputs</dt><dd>{coverage.acquisition.validatedScienceInputCount}</dd></div>
            <div><dt>Comparison-ready</dt><dd>{coverage.acquisition.comparisonReadyCount}</dd></div>
          </dl>
        </section>
      )}

      {objectiveAudit && (
        <section className={styles.acquisition} aria-labelledby="family-status-title">
          <div>
            <span>CROSS-FAMILY DEMONSTRATION STATUS</span>
            <h3 id="family-status-title">Real Rubin-versus-survey pixels by science family</h3>
            <p>{objectiveAudit.objectiveAchieved ? "Every objective gate is verified." : `${Object.values(objectiveAudit.pixelFamilyRegionCounts).filter((count) => count > 0).length} of ${Object.keys(objectiveAudit.pixelFamilyRegionCounts).length} required pixel families currently have a real display example. Exact coverage is still unresolved for ${objectiveAudit.unresolvedCoverageSurveyIds.length} release-matched products.`}</p>
          </div>
          <dl>
            {Object.entries(objectiveAudit.pixelFamilyRegionCounts).map(([family, count]) => <div key={family}><dt>{categoryLabel(family)}</dt><dd>{count}</dd></div>)}
          </dl>
        </section>
      )}

      <div className={styles.controls} id="coverage-controls">
        <label className={styles.search}>
          <span className={styles.srOnly}>Search tracts and surveys</span>
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search tract, field, or survey…"
          />
        </label>
        <label>
          <span>Dataset category</span>
          <select value={category} onChange={(event) => setCategory(event.target.value)}>
            <option value="all">All categories</option>
            {categories.map((item) => <option key={item} value={item}>{categoryLabel(item)}</option>)}
          </select>
        </label>
        <fieldset className={styles.scope}>
          <legend>Show tracts</legend>
          {([
            ["all", "All"],
            ["overlap", "Any overlap"],
            ["pixels", "Pixels cached"],
            ["no-overlap", "No overlap"],
          ] as const).map(([value, label]) => (
            <button key={value} type="button" aria-pressed={scope === value} onClick={() => setScope(value)}>{label}</button>
          ))}
        </fieldset>
      </div>

      <div className={styles.workspace}>
        <div className={styles.skyPanel}>
          <div className={styles.panelHeading}>
            <div><span>FULL-FOOTPRINT OVERVIEW</span><strong>{formatCount(filteredTracts.length)} tracts shown</strong></div>
            <div className={styles.legend} aria-label="Map legend">
              <span><i className={styles.pixelDot} /> Pixels cached</span>
              <span><i className={styles.coverageDot} /> Exact archive/MOC overlap</span>
              <span><i className={styles.subsetDot} /> Exact released-product subset</span>
              <span><i className={styles.emptyDot} /> Rubin only</span>
            </div>
          </div>
          <div className={styles.skyPlot} role="group" aria-label="Rubin tract positions in equatorial coordinates">
            <span className={styles.axisRa}>Right ascension: 360° → 0°</span>
            <span className={styles.axisDec}>Declination</span>
            {filteredTracts.map((tract) => {
              const pixelCount = tract.pixelCachedSurveyIds?.length ?? 0;
              const subsetCount = tract.subsetSurveyIds?.length ?? 0;
              const tone = pixelCount > 0 ? styles.pixelPin : tract.overlapSurveyIds.length > 0 ? styles.coveragePin : subsetCount > 0 ? styles.subsetPin : styles.emptyPin;
              const pinStyle = {
                "--x": `${((360 - ((tract.raDeg % 360) + 360) % 360) / 360) * 100}%`,
                "--y": `${((90 - Math.max(-90, Math.min(90, tract.decDeg))) / 180) * 100}%`,
              } as CSSProperties;
              return (
                <button
                  type="button"
                  key={tract.id}
                  className={`${styles.tractPin} ${tone}${activeTract?.id === tract.id ? ` ${styles.selectedPin}` : ""}`}
                  style={pinStyle}
                  aria-label={`${tract.label ?? `Tract ${tract.id}`}: ${tract.overlapSurveyIds.length} exact archive or MOC overlaps, ${subsetCount} exact product subsets, ${pixelCount} pixel layers cached`}
                  aria-pressed={activeTract?.id === tract.id}
                  onClick={() => selectTract(tract)}
                >
                  <span>{tract.id}</span>
                </button>
              );
            })}
            {filteredTracts.length === 0 && <div className={styles.emptyState}>No tracts match these filters.</div>}
          </div>
          <p className={styles.plotNote}>Coordinate overview derived only from supplied tract centers. It is not sky imagery and does not imply continuous coverage between markers.</p>
        </div>

        <aside className={styles.detailPanel} aria-live="polite">
          {activeTract ? (
            <>
              <div className={styles.detailHeading}>
                <div><span>SELECTED TRACT</span><h3>{activeTract.label ?? activeTract.id}</h3></div>
                <strong>{activeTract.raDeg.toFixed(4)}°, {activeTract.decDeg.toFixed(4)}°</strong>
              </div>
              {activeTract.bands?.length ? <p className={styles.bands}>Rubin bands: {activeTract.bands.join(" · ")}</p> : null}
              <div className={styles.tractStats}>
                <span><strong>{activeTract.overlapSurveyIds.length}</strong> exact overlaps</span>
                <span><strong>{activeTract.subsetSurveyIds?.length ?? 0}</strong> product subsets</span>
                <span><strong>{activeTract.pixelCachedSurveyIds?.length ?? 0}</strong> pixel layers cached</span>
                <span><strong>{activeTract.selectedRegionCount ?? 0}</strong> acquisition candidates</span>
              </div>
              {activeTract.previewImage && (
                <div className={styles.tractPreview}>
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={activeTract.previewImage} alt={`Rubin pixels in ${activeTract.label ?? activeTract.id}`} />
                  {(activeTract.layerThumbnails ?? []).length > 0 && (
                    <div className={styles.tractThumbs}>
                      {(activeTract.layerThumbnails ?? []).slice(0, 6).map((thumb) => (
                        <figure key={`${thumb.surveyName}-${thumb.band}`}>
                          {/* eslint-disable-next-line @next/next/no-img-element */}
                          <img src={thumb.image} alt={`${thumb.surveyName} ${thumb.band} at this position`} />
                          <figcaption>{thumb.surveyName}<small>{thumb.band}</small></figcaption>
                        </figure>
                      ))}
                    </div>
                  )}
                </div>
              )}
              {activeTract.href && <a className={styles.tractLink} href={activeTract.href}>{activeTract.viewerStatus === "aligned-viewer" ? "Open aligned image & overlay viewer" : activeTract.viewerStatus === "image-only" ? "Open validated image workspace" : "Open tract evidence workspace"} →</a>}
              <h4>Overlapping datasets</h4>
              <div className={styles.overlapList}>
                {activeTract.overlapSurveyIds.map((surveyId) => {
                  const survey = surveyById.get(surveyId);
                  const pixelReady = activeTract.pixelCachedSurveyIds?.includes(surveyId) ?? false;
                  return (
                    <div className={styles.overlapRow} key={surveyId} data-survey-id={surveyId}>
                      <span><strong>{survey?.shortName ?? survey?.name ?? surveyId}</strong><small>{survey ? categoryLabel(survey.category) : "Unregistered dataset"}</small></span>
                      <span className={styles.overlapAction}><em className={pixelReady ? styles.readyStatus : styles.coverageStatus}>{pixelReady ? "PIXELS CACHED" : "EXACT OVERLAP"}</em>{survey && <button type="button" onClick={() => inspectSurvey(survey)}>Filter map →</button>}</span>
                    </div>
                  );
                })}
                {(activeTract.subsetSurveyIds ?? []).map((surveyId) => {
                  const survey = surveyById.get(surveyId);
                  return (
                    <div className={styles.overlapRow} key={`subset-${surveyId}`} data-survey-id={surveyId}>
                      <span><strong>{survey?.shortName ?? survey?.name ?? surveyId}</strong><small>{survey ? categoryLabel(survey.category) : "Unregistered dataset"}</small></span>
                      <span className={styles.overlapAction}><em className={styles.coverageStatus}>PRODUCT SUBSET</em>{survey && <button type="button" onClick={() => inspectSurvey(survey)}>Filter map →</button>}</span>
                    </div>
                  );
                })}
                {activeTract.overlapSurveyIds.length === 0 && (activeTract.subsetSurveyIds?.length ?? 0) === 0 && <p>No external footprint intersections are recorded for this tract.</p>}
              </div>
            </>
          ) : (
            <div className={styles.promptState}><span>TRACT DETAILS</span><strong>Select a tract to inspect its dataset overlaps.</strong></div>
          )}
        </aside>
      </div>

      <section className={styles.registry} aria-labelledby="dataset-registry-title">
        <div className={styles.registryHeading}>
          <div><span>DATASET REGISTRY</span><h3 id="dataset-registry-title">Overlap sources by category</h3></div>
          <p>Counts below come from the supplied coverage index, not from visual estimates.</p>
        </div>
        <div className={styles.registryGrid}>
          {surveys.map((survey) => (
            <article key={survey.id}>
              <div className={styles.surveyTop}>
                <span>{categoryLabel(survey.category)}</span>
                <em data-status={survey.availability}>{AVAILABILITY_LABELS[survey.availability]}</em>
              </div>
              <h4>{survey.name}</h4>
              <p>{survey.release ?? "Release not supplied"}{survey.description ? ` · ${survey.description}` : ""}</p>
              <div className={styles.surveyCounts}>
                <span><strong>{formatCount(survey.overlappingTractCount)}</strong> exact overlap</span>
                {survey.subsetTractCount !== undefined && <span title={survey.subsetLabel}><strong>{formatCount(survey.subsetTractCount)}</strong> product subset</span>}
                <span><strong>{formatCount(survey.pixelCachedTractCount)}</strong> pixels cached</span>
              </div>
              <div className={styles.cardActions}>
                <button type="button" onClick={() => inspectSurvey(survey)}>Filter map</button>
                {survey.sourceUrl && <a href={survey.sourceUrl} target="_blank" rel="noreferrer">Source ↗</a>}
              </div>
              {survey.coverageEvidence && <span className={styles.evidence}>Coverage evidence: {survey.coverageEvidence.replace("-", " ")}</span>}
              {survey.coverageUpdatedAt && <small>Coverage checked {displayDate(survey.coverageUpdatedAt)}</small>}
            </article>
          ))}
          {surveys.length === 0 && <div className={styles.emptyRegistry}>No external datasets have been registered yet.</div>}
        </div>
      </section>
    </section>
  );
}

export default CoverageExplorer;
