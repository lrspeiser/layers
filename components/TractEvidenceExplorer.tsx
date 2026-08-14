import Image from "next/image";
import Link from "next/link";
import styles from "./TractEvidenceExplorer.module.css";

type Download = { href: string; productType: string; bytes: number; sha256: string };
export type TractEvidenceRoute = {
  tract: number;
  href: string;
  centerDeg: number[];
  families: string[];
  evidence: Array<{ id: string; family: string; productType: string; jsonHref: string; previewHref?: string; downloads: Download[] }>;
  unresolved: Array<{ family: string; status: string; jsonHref: string }>;
};

type SpectrumEvidence = {
  release: string;
  productType: string;
  spatialMetadata: { coordinateFrame: string; wcs: string };
  spectrum: {
    raDeg: number; decDeg: number; separationFromTractCenterArcmin: number; spectype: string;
    redshift: number; redshiftError: number; redshiftWarning: number; samples: number;
    wavelengthRangeAngstrom: number[]; units: { wavelength: string; flux: string };
  };
  interpretation: { status: string; statement: string; comparisonClaim: null; requiredBeforeRubinAssociation: string[] };
  provenance: { service: string; desiDataAccess: string; releaseDocumentation: string };
};

type XrayRecord = {
  name: string; ra: number; dec: number; error_radius: number; source_extent: number;
  b0_detect_likelihood: number; b1_flux: number; b1_flux_error: number; separationArcmin: number;
};
type XrayEvidence = {
  release: string; productType: string; recordCount: number; records: XrayRecord[];
  query: { centerDeg: number[]; radiusDeg: number };
  spatialMetadata: { coordinateFrame: string; wcs: string };
  interpretation: { status: string; statement: string; comparisonClaim: null; requiredBeforeDifferenceAnalysis: string[] };
  provenance: { service: string; catalogDocumentation: string; archiveDocumentation: string };
};

type HiRecord = {
  HIPASS: string; RAJ2000: number; DEJ2000: number; RVmom: number; W50max: number;
  Speak: number; Sint: number; RMS: number; Qual: number; SimbadName: string; separationArcmin: number;
};
type HiEvidence = {
  release: string; productType: string; recordCount: number; records: HiRecord[];
  query: { centerDeg: number[]; radiusDeg: number };
  spatialMetadata: { coordinateFrame: string; wcs: string };
  interpretation: { status: string; statement: string; comparisonClaim: null; requiredBeforeDifferenceAnalysis: string[] };
  provenance: { service: string; catalog: string; catalogReadme: string };
};

type LensingEvidence = { status: string; reason: string; notSubstitutedWith: string; nextAuthoritativeRoutes: string[] };

type Props = {
  route: TractEvidenceRoute;
  spectrum?: SpectrumEvidence;
  xray?: XrayEvidence;
  neutralGas?: HiEvidence;
  lensing?: LensingEvidence;
};

function humanBytes(bytes: number) {
  return bytes > 500_000 ? `${(bytes / 1_000_000).toFixed(2)} MB` : `${(bytes / 1_000).toFixed(1)} kB`;
}

function DownloadLinks({ evidence }: { evidence: TractEvidenceRoute["evidence"][number] }) {
  return <div className={styles.downloads}>
    <a href={evidence.jsonHref}>Evidence JSON</a>
    {evidence.downloads.map((download) => <a href={download.href} key={download.href}>{download.productType} · {humanBytes(download.bytes)}</a>)}
  </div>;
}

function PositionPlot({ center, radius, points, label }: { center: number[]; radius: number; points: Array<{ ra: number; dec: number; title: string; strength?: number }>; label: string }) {
  const size = 320;
  const project = (ra: number, dec: number) => ({
    x: size / 2 + ((ra - center[0]) * Math.cos(center[1] * Math.PI / 180) / radius) * (size * 0.43),
    y: size / 2 - ((dec - center[1]) / radius) * (size * 0.43),
  });
  return <figure className={styles.positionPlot}>
    <svg viewBox={`0 0 ${size} ${size}`} role="img" aria-label={label}>
      <circle cx="160" cy="160" r="138" className={styles.cone} />
      <path d="M150 160h20M160 150v20" className={styles.centerMark} />
      {points.map((point, index) => {
        const at = project(point.ra, point.dec);
        const radiusPx = Math.min(7, Math.max(3, point.strength ? 2 + Math.log10(Math.max(1, point.strength)) : 4));
        return <circle key={`${point.title}-${index}`} cx={at.x} cy={at.y} r={radiusPx}><title>{point.title}</title></circle>;
      })}
    </svg>
    <figcaption>ICRS cone · cross marks Rubin tract center · dots are catalog positions, not image pixels</figcaption>
  </figure>;
}

function Issues({ items }: { items: string[] }) {
  return <div className={styles.issues}><strong>Issues before comparison</strong><ul>{items.map((item) => <li key={item}>{item}</li>)}</ul></div>;
}

export function TractEvidenceExplorer({ route, spectrum, xray, neutralGas, lensing }: Props) {
  const realProductCount = route.evidence.length;
  return <section className={styles.explorer}>
    <header className={styles.hero}>
      <div><span className={styles.eyebrow}>TRACT FAMILY EXPLORER · REAL ARCHIVE PRODUCTS</span><h1>Rubin tract {route.tract}</h1><p>One sky region, multiple kinds of evidence. Each section states what the product contains and what it cannot yet prove.</p></div>
      <dl><div><dt>Center</dt><dd>{route.centerDeg[0].toFixed(4)}°, {route.centerDeg[1].toFixed(4)}°</dd></div><div><dt>Real products</dt><dd>{realProductCount}</dd></div><div><dt>Families</dt><dd>{route.families.length}</dd></div></dl>
    </header>

    <div className={styles.truth}><strong>OVERLAP EVIDENCE, NOT A DIFFERENCE MEASUREMENT</strong><span>The products are real and positionally relevant. Rubin-only or reference-only physical signals require registration, selection-function, PSF/beam, mask, and uncertainty controls.</span></div>

    <nav className={styles.localNav} aria-label="Tract evidence sections">
      {spectrum && <a href="#spectroscopy">Spectrum</a>}
      {xray && <a href="#xray">X-ray catalog</a>}
      {neutralGas && <a href="#neutral-gas">H I catalog</a>}
      {lensing && <a href="#lensing">Lensing status</a>}
      <Link href="/">Back to all tracts</Link>
    </nav>

    {spectrum && <article className={styles.family} id="spectroscopy">
      <div className={styles.familyHeading}><div><span>SPECTROSCOPY · {spectrum.productType.toUpperCase()}</span><h2>DESI turns one point of light into a measured spectrum</h2></div><b className={styles.real}>REAL PRODUCT</b></div>
      <div className={styles.goalGrid}><section><strong>Goal</strong><p>Expose wavelength-resolved information and a pipeline redshift at a position inside this Rubin tract.</p></section><section><strong>Details</strong><p>{spectrum.spectrum.samples.toLocaleString()} samples from {spectrum.spectrum.wavelengthRangeAngstrom[0].toLocaleString()}–{spectrum.spectrum.wavelengthRangeAngstrom[1].toLocaleString()} Å. Object class: {spectrum.spectrum.spectype}; redshift warning: {spectrum.spectrum.redshiftWarning}.</p></section><section><strong>What it adds</strong><p>{spectrum.interpretation.statement}</p></section></div>
      <div className={styles.spectrumGrid}>
        <figure className={styles.spectrum}><Image src="/layer-previews/family-examples/desi-edr-tract-9813-spectrum.png" width={1600} height={608} sizes="(max-width: 900px) 100vw, 70vw" alt={`DESI spectrum and fitted model in Rubin tract ${route.tract}`} /><figcaption>Observed DESI flux in gray; Redrock model in coral. This is a one-dimensional calibrated spectrum, not an optical image.</figcaption></figure>
        <dl className={styles.facts}><div><dt>Release</dt><dd>{spectrum.release}</dd></div><div><dt>Redshift</dt><dd>{spectrum.spectrum.redshift.toFixed(6)} ± {spectrum.spectrum.redshiftError.toExponential(2)}</dd></div><div><dt>Sky position</dt><dd>{spectrum.spectrum.raDeg.toFixed(6)}°, {spectrum.spectrum.decDeg.toFixed(6)}°</dd></div><div><dt>From tract center</dt><dd>{spectrum.spectrum.separationFromTractCenterArcmin.toFixed(2)} arcmin</dd></div><div><dt>Spatial model</dt><dd>{spectrum.spatialMetadata.coordinateFrame}; {spectrum.spatialMetadata.wcs}</dd></div></dl>
      </div>
      <Issues items={spectrum.interpretation.requiredBeforeRubinAssociation} />
      <DownloadLinks evidence={route.evidence.find((item) => item.family === "spectroscopy")!} />
    </article>}

    {xray && <article className={styles.family} id="xray">
      <div className={styles.familyHeading}><div><span>HIGH ENERGY · {xray.productType.toUpperCase()}</span><h2>eROSITA detections mark X-ray-emitting sources</h2></div><b className={styles.real}>REAL PRODUCT</b></div>
      <div className={styles.goalGrid}><section><strong>Goal</strong><p>Show whether the same tract contains public X-ray detections that add a different physical observable.</p></section><section><strong>Details</strong><p>{xray.recordCount} eRASS1 sources inside a {xray.query.radiusDeg}° cone. Positions and fluxes come from the HEASARC catalog response.</p></section><section><strong>What it adds</strong><p>{xray.interpretation.statement}</p></section></div>
      <div className={styles.catalogGrid}>
        <PositionPlot center={xray.query.centerDeg} radius={xray.query.radiusDeg} label={`${xray.recordCount} eROSITA catalog positions around tract ${route.tract}`} points={xray.records.map((row) => ({ ra: row.ra, dec: row.dec, title: `${row.name}; ${row.b1_flux.toExponential(2)} erg s-1 cm-2`, strength: row.b0_detect_likelihood }))} />
        <div className={styles.tableWrap}><table><thead><tr><th>Source</th><th>Offset</th><th>Detection likelihood</th><th>Band-1 flux</th></tr></thead><tbody>{xray.records.slice(0, 8).map((row) => <tr key={row.name}><td>{row.name}</td><td>{row.separationArcmin.toFixed(1)}′</td><td>{row.b0_detect_likelihood.toFixed(1)}</td><td>{row.b1_flux.toExponential(2)}</td></tr>)}</tbody></table><p>Strongest eight shown · flux unit: erg s⁻¹ cm⁻² in the 0.2–0.6 keV band.</p></div>
      </div>
      <Issues items={xray.interpretation.requiredBeforeDifferenceAnalysis} />
      <DownloadLinks evidence={route.evidence.find((item) => item.family === "high-energy")!} />
    </article>}

    {neutralGas && <article className={styles.family} id="neutral-gas">
      <div className={styles.familyHeading}><div><span>NEUTRAL GAS · {neutralGas.productType.toUpperCase()}</span><h2>HIPASS records a 21-cm H I line detection</h2></div><b className={styles.real}>REAL PRODUCT</b></div>
      <div className={styles.goalGrid}><section><strong>Goal</strong><p>Expose neutral-hydrogen evidence in the same selected Rubin tract region.</p></section><section><strong>Details</strong><p>{neutralGas.recordCount} HICAT detection within {neutralGas.query.radiusDeg}°. The catalog supplies velocity, linewidth, peak flux density, and integrated line flux.</p></section><section><strong>What it adds</strong><p>{neutralGas.interpretation.statement}</p></section></div>
      <div className={styles.catalogGrid}>
        <PositionPlot center={neutralGas.query.centerDeg} radius={neutralGas.query.radiusDeg} label={`HIPASS catalog position around tract ${route.tract}`} points={neutralGas.records.map((row) => ({ ra: row.RAJ2000, dec: row.DEJ2000, title: `${row.HIPASS}; ${row.Sint} Jy km/s`, strength: row.Sint }))} />
        <div className={styles.hiCard}>{neutralGas.records.map((row) => <dl key={row.HIPASS}><div><dt>Source</dt><dd>{row.HIPASS}</dd></div><div><dt>Integrated H I flux</dt><dd>{row.Sint} Jy km/s</dd></div><div><dt>Velocity moment</dt><dd>{row.RVmom.toFixed(1)} km/s</dd></div><div><dt>50% linewidth</dt><dd>{row.W50max.toFixed(1)} km/s</dd></div><div><dt>From tract center</dt><dd>{row.separationArcmin.toFixed(2)} arcmin</dd></div><div><dt>Catalog quality</dt><dd>{row.Qual}</dd></div></dl>)}</div>
      </div>
      <Issues items={neutralGas.interpretation.requiredBeforeDifferenceAnalysis} />
      <DownloadLinks evidence={route.evidence.find((item) => item.family === "neutral-gas")!} />
    </article>}

    {lensing && <article className={`${styles.family} ${styles.unresolved}`} id="lensing">
      <div className={styles.familyHeading}><div><span>LENSING · NO VALIDATED PRODUCT</span><h2>Intentionally unresolved</h2></div><b>UNRESOLVED</b></div>
      <div className={styles.goalGrid}><section><strong>Goal</strong><p>Add a real shear or convergence product overlapping a selected Rubin tract.</p></section><section><strong>Details</strong><p>{lensing.reason}</p></section><section><strong>Issue</strong><p>{lensing.notSubstitutedWith}</p></section></div>
      <Issues items={lensing.nextAuthoritativeRoutes} />
      <div className={styles.downloads}><a href="/data/layers/family-examples/lensing.json">Unresolved evidence JSON</a></div>
    </article>}

    <footer className={styles.footer}><span>Integrity</span><p>Every downloadable table is checksum-addressed in the <a href="/data/layers/family-examples/tract-manifest.json">tract evidence manifest</a>. Catalog markers are never rendered as image pixels.</p></footer>
  </section>;
}
