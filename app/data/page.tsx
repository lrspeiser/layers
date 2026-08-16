import type { Metadata } from "next";
import Link from "next/link";
import styles from "./page.module.css";
import release from "@/public/data/layers/selected-regions/catalogue-release.json";
import reliability from "@/public/data/layers/selected-regions/catalogue-reliability.json";
import synthetic from "@/public/data/layers/selected-regions/synthetic-bandpass.json";
import moc from "@/public/data/layers/selected-regions/coverage-moc.json";
import bias from "@/public/data/layers/selected-regions/aperture-bias.json";
// The slim companion: the full file carries a per-region block and is ~750 kB,
// which a page import would ship straight into the worker bundle.
import covariance from "@/public/data/layers/selected-regions/resampling-covariance-slim.json";
import segments from "@/public/data/layers/selected-regions/segment-noise-inflation.json";

export const metadata: Metadata = {
  title: "Data access",
  description:
    "Download the Rubin-versus-reference source catalogue, query it by cone search, and read what its numbers mean.",
};

const megabytes = (bytes: number | null | undefined) =>
  typeof bytes === "number" ? `${(bytes / 1e6).toFixed(1)} MB` : "—";

export default function DataPage() {
  const columns = Object.entries(release.columns) as Array<
    [string, { unit: string; ucd: string; description: string }]
  >;
  const measuredCoverage = moc.surveys.filter((s) => s.servedMeasured);

  return (
    <main id="top">
      <header className="layers-header">
        <Link className="layers-brand" href="/">
          <span className="brand-glyph">
            <i />
            <b />
          </span>
          <strong>Layers</strong>
          <small>science comparison workspace</small>
        </Link>
        <nav>
          <Link href="/">Full footprint</Link>
          <Link href="/explorer">Difference explorer</Link>
          <Link href="/differences">Operators</Link>
          <Link href="/goals">Goals</Link>
        </nav>
        <span className="release-chip">DATA</span>
      </header>

      <section className={styles.page}>
        <header className={styles.intro}>
          <span>DATA ACCESS</span>
          <h1>{release.rows.toLocaleString()} sources, measured on identical pixels</h1>
          <p>
            Every source detected across {release.regions} reconciled regions, with Rubin and a
            reference survey measured through the <em>same</em> segmentation — so a row&rsquo;s two
            fluxes come from the same pixels and no cross-match error enters the difference.
            Download it, or query it by position.
          </p>
        </header>

        <div className={styles.grid}>
          <article className={styles.card}>
            <h2>Download</h2>
            <ul className={styles.files}>
              <li>
                <a href={release.files.parquet.path} download>
                  Parquet
                </a>
                <span>{megabytes(release.files.parquet.bytes)}</span>
                <small>{release.files.parquet.use}</small>
              </li>
              {release.files.votableGzip && (
                <li>
                  <a href={release.files.votableGzip.path} download>
                    VOTable (gzip)
                  </a>
                  <span>{megabytes(release.files.votableGzip.bytes)}</span>
                  <small>{release.files.votableGzip.use}</small>
                </li>
              )}
            </ul>
            <p className={styles.checksum}>
              SHA-256 <code>{release.files.parquet.sha256.slice(0, 16)}…</code>
            </p>
          </article>

          <article className={styles.card}>
            <h2>Query by position</h2>
            <p>
              IVOA Simple Cone Search. The same URL opens in TOPCAT and Aladin, and returns a
              VOTable with the UCDs clients locate positions by.
            </p>
            <pre>
              <code>{`from pyvo.dal import SCSService

svc = SCSService("https://rubin-light-atlas.vercel.app${release.coneSearch.endpoint}")
tbl = svc.search(pos=(53.08, -27.49), radius=0.05).to_table()
tbl.sort("departure_significance")`}</code>
            </pre>
            <p className={styles.muted}>
              {release.coneSearch.tiles.count} spatial tiles, one degree square. A cone touches one
              or a few, so a query reads only the sky it asked about.
            </p>
          </article>

          <article className={styles.card}>
            <h2>Which column to cut on</h2>
            <p className={styles.emphasis}>departure_significance</p>
            <p>{release.whichSignificance}</p>
          </article>

          <article className={styles.card} data-tone="caution">
            <h2>Extended sources are biased</h2>
            <p>{release.extendedSourceBias}</p>
            <dl className={styles.stats}>
              <div>
                <dt>rubin-convolved</dt>
                <dd>{bias.groups["rubin-convolved"].medianSizeBias.toFixed(3)}</dd>
              </div>
              <div>
                <dt>reference-convolved</dt>
                <dd>{bias.groups["reference-convolved"].medianSizeBias.toFixed(3)}</dd>
              </div>
              <div>
                <dt>p</dt>
                <dd>{bias.kruskalWallisP?.toExponential(1)}</dd>
              </div>
            </dl>
          </article>

          <article className={styles.card}>
            <h2>Errors corrected for correlated noise</h2>
            <p>
              These flux errors are not raw photutils values. <code>segment_fluxerr</code> is
              &sigma;&middot;&radic;N, which assumes independent pixels; image noise is correlated
              (lag-1 {covariance.summary.reference.medianLag1Autocorrelation?.toFixed(2)} in the
              resampled reference), so a real aperture sum scatters far more. Measured by
              translating {segments.segmentsMeasured.toLocaleString()} of this catalogue&rsquo;s own
              segment footprints around blank sky, then applied.
            </p>
            <dl className={styles.stats}>
              <div>
                <dt>errors raised by</dt>
                <dd>&times;{segments.overall.medianErrorBarUnderstatedBy.toFixed(2)}</dd>
              </div>
              <div>
                <dt>within measured range</dt>
                <dd>
                  {(segments.coverage.catalogueFractionWithinMeasuredRange * 100).toFixed(1)}%
                </dd>
              </div>
            </dl>
            <p className={styles.muted}>
              The factor rises with segment area, so it is applied per source and recorded in{" "}
              <code>noise_inflation_factor</code> &mdash; divide the error columns by its square
              root to recover the uncorrected values. Above{" "}
              {segments.coverage.largestAreaMeasuredPixels} pixels the curve is held flat rather
              than extrapolated, which understates the correction, and those rows carry{" "}
              <code>flag_inflation_extrapolated</code>. <code>departure_significance</code> was
              never affected: it divides by the field&rsquo;s own measured scatter and never
              assumed independent pixels.
            </p>
          </article>

          <article className={styles.card}>
            <h2>How complete, how reliable</h2>
            <dl className={styles.stats}>
              <div>
                <dt>90% complete to</dt>
                <dd>{reliability.counts.median90PercentCompleteMagAB} mag AB</dd>
              </div>
              <div>
                <dt>false-positive rate</dt>
                <dd>
                  {reliability.counts.rateIsAnUpperLimit ? "< " : ""}
                  {(
                    (reliability.counts.rateIsAnUpperLimit
                      ? reliability.counts.falsePositiveRate95UpperLimit
                      : reliability.counts.falsePositiveRate) * 100
                  ).toFixed(2)}
                  %
                </dd>
              </div>
            </dl>
            <p className={styles.muted}>
              Measured by injecting synthetic sources into both frames with the field&rsquo;s own
              flux ratio, so they carry no departure by construction, and detected the same way the
              catalogue detects &mdash; on the sum of both frames.
              {reliability.counts.rateIsAnUpperLimit
                ? " No false positive was seen, so this is the 95% upper limit rather than a rate of zero."
                : ""}{" "}
              {release.reliability.note}
            </p>
          </article>

          <article className={styles.card}>
            <h2>Coverage, as a MOC</h2>
            <p>
              Where the pixels actually are, not where a footprint claims. Load in Aladin or
              intersect with your own coverage in <code>mocpy</code> before fetching anything.
            </p>
            <ul className={styles.files}>
              {measuredCoverage.map((survey) => (
                <li key={survey.surveyId}>
                  <a href={survey.servedMoc} download>
                    {survey.surveyId}
                  </a>
                  <span>{survey.servedRegions} regions</span>
                  <small>
                    {survey.claimedRegions} claimed by footprint overlap, {survey.servedRegions} with
                    pixels
                  </small>
                </li>
              ))}
            </ul>
          </article>

          <article className={styles.card} data-tone="caution">
            <h2>What a large departure is not</h2>
            <p>{release.caveat}</p>
            <p className={styles.muted}>
              The filter colour term is measured directly from CALSPEC spectra and official
              transmission curves:{" "}
              {synthetic.predictedColourTerms["legacy-surveys-dr10"].predictedColourTermPerMag} mag
              per mag against DECam, linear to{" "}
              {synthetic.predictedColourTerms["legacy-surveys-dr10"].residualRmsMag} mag.
            </p>
          </article>
        </div>

        <div className={styles.dictionary}>
          <h2>Columns</h2>
          <div className={styles.table}>
            <table>
              <thead>
                <tr>
                  <th scope="col">name</th>
                  <th scope="col">unit</th>
                  <th scope="col">UCD</th>
                  <th scope="col">meaning</th>
                </tr>
              </thead>
              <tbody>
                {columns.map(([name, meta]) => (
                  <tr key={name} data-highlight={name === "departure_significance"}>
                    <td>
                      <code>{name}</code>
                    </td>
                    <td>{meta.unit || "—"}</td>
                    <td>
                      <code>{meta.ucd}</code>
                    </td>
                    <td>{meta.description}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <p className={styles.reproduce}>
          Reproduce any of this: <code>pip install -r requirements.txt</code>, then the scripts in{" "}
          <code>pipeline/</code>. Rubin pixels need <code>RUBIN_RSP_TOKEN</code> and data rights;
          every other input comes from a public archive, and the manifests carry SHA-256 checksums
          for what was fetched.
        </p>
      </section>
    </main>
  );
}
