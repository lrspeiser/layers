import styles from "./DifferenceIndex.module.css";

// The six gates a Rubin-versus-reference product must clear before a difference
// measured from it can be called quantitative. Order matches the pipeline.
export const GATES = [
  "PSF matching",
  "background matching",
  "flux-unit transfer",
  "bandpass transfer",
  "resampling covariance",
  "injection/recovery QA",
] as const;

export type OperatorCard = {
  id: string;
  label: string;
  kind: string;
  headline: string;
  detail: string;
  state: "measured" | "partial" | "none";
};

export type GateRow = {
  regionId: string;
  cleared: string[];
};

export type AnomalyRow = {
  regionId: string;
  tract: number;
  effectiveRadiusArcsec: number;
  empiricalSigma: number;
  amplitudeNjy: number;
  direction: string;
  scalesDetected?: number;
  sky: { raDeg: number; decDeg: number };
};

export type AttributionRow = { question: string; verdict: string; basis: string };

export type ChainFlag = {
  reference: string;
  medianScale: number;
  departureMag: number;
  reading: string;
  toResolve: string;
};

export function DifferenceIndex({
  operators,
  gates,
  anomalies,
  anomalyContext,
  attribution = [],
  register,
  chainFlags = [],
}: {
  operators: OperatorCard[];
  gates: GateRow[];
  anomalies: AnomalyRow[];
  anomalyContext: { scanned: number; skipped: number; raw: number; surviving: number };
  attribution?: AttributionRow[];
  register?: { candidates: number; evaluated: number; confirmed: number };
  chainFlags?: ChainFlag[];
}) {
  const fullyCleared = gates.filter((row) => row.cleared.length === GATES.length).length;
  return (
    <section className={styles.index}>
      <header>
        <span>CROSS-SURVEY DIFFERENCES</span>
        <h1>What changes when another survey looks</h1>
        <p>
          Every comparison operator, the gates each product has cleared, and the residuals that no
          known effect explains. A difference is an observation, not a claim that earlier science was
          wrong, and nothing here has cleared every gate.
        </p>
      </header>

      <p className={styles.truth} role="note">
        <strong>None of this is a detection.</strong>
        <span>
          Bandpass transfer is not validated, so a colour difference alone can produce a residual of
          the size listed below. Candidates are ranked places to look, each carrying the tests that
          would rule it out.
        </span>
      </p>

      <div className={styles.operators}>
        {operators.map((operator) => (
          <article key={operator.id}>
            <span>{operator.kind.toUpperCase()}</span>
            <strong>{operator.headline}</strong>
            <p>{operator.detail}</p>
            <em data-state={operator.state}>
              {operator.state === "measured" ? "MEASURED" : operator.state === "partial" ? "PARTIAL" : "NOT STARTED"}
            </em>
          </article>
        ))}
      </div>

      <div className={styles.section}>
        <h2>Gate progress per region</h2>
        <p>
          {fullyCleared} of {gates.length} reconciled regions have cleared all six gates.
          Clearing a gate means the operation was applied and its check passed on that region, not
          that the comparison is publishable.
        </p>
        <div className={styles.gates}>
          {gates.slice(0, 60).map((row) => (
            <div className={styles.gateRow} key={row.regionId}>
              <strong>{row.regionId.replace("dp2-tract-", "Tract ")}</strong>
              <div className={styles.gateTrack} role="img" aria-label={`${row.cleared.length} of ${GATES.length} gates cleared`}>
                {GATES.map((gate) => (
                  <i key={gate} data-cleared={row.cleared.includes(gate)} title={gate} />
                ))}
              </div>
            </div>
          ))}
        </div>
        <div className={styles.gateLegend}>
          <span><i /> cleared</span>
          <span><i data-open="true" /> open</span>
          {GATES.map((gate, index) => (
            <span key={gate}>{index + 1}. {gate}</span>
          ))}
        </div>
      </div>

      {attribution.length > 0 && (
        <div className={styles.section}>
          <h2>Which survey does the difference belong to?</h2>
          <p>
            A single reference can measure a difference but cannot say who owns it. Legacy Survey,
            DES DR2 and Pan-STARRS are independently calibrated and were reduced by different
            pipelines, so across the sky they share, Rubin is the only term every comparison has in
            common. Each answer below is what that shared term allows, and no more. Where a
            reference dissents it is named rather than averaged in.
          </p>
          {chainFlags.length > 0 && (
            <div className={styles.chainFlags} role="note">
              {chainFlags.map((flag) => (
                <p key={flag.reference}>
                  <strong>{flag.reference.toUpperCase()} is excluded from the zeropoint test.</strong>{" "}
                  Its median scale is {flag.medianScale.toFixed(4)}, a {Math.abs(flag.departureMag).toFixed(3)} mag
                  departure from the references whose flux chain has been verified. {flag.reading} {flag.toResolve}
                </p>
              ))}
            </div>
          )}
          <div className={styles.attribution}>
            {attribution.map((row) => (
              <article key={row.question}>
                <h3>{row.question}</h3>
                <strong>{row.verdict}</strong>
                <p>{row.basis}</p>
              </article>
            ))}
          </div>
        </div>
      )}

      <div className={styles.section}>
        <h2>Residuals with no boring explanation</h2>
        {register && register.evaluated > 0 && (
          <p className={styles.denominator}>
            Across every operator, {register.candidates} candidates come out of{" "}
            {register.evaluated.toLocaleString()} comparisons evaluated, and {register.confirmed} are
            flagged by more than one operator. The denominator is stated because a candidate count
            on its own says nothing about how unusual anything is.
          </p>
        )}
        <p>
          {anomalyContext.raw} candidates were found across {anomalyContext.scanned} scannable
          regions ({anomalyContext.skipped} regions could not be scanned). {anomalyContext.surviving}{" "}
          survive the mask-edge, PSF-wing, single-scale, and crowded-field checks. Significance is
          measured against the empirical blank-position scatter, never the per-pixel variance planes,
          which understate the true uncertainty by a median factor of about seven.
        </p>
        {anomalies.length === 0 ? (
          <p className={styles.empty}>No candidate survived every check in the current scan.</p>
        ) : (
          <div className={styles.table}>
            <table>
              <thead>
                <tr>
                  <th scope="col">Region</th>
                  <th scope="col">Scale</th>
                  <th scope="col">Empirical σ</th>
                  <th scope="col">Amplitude</th>
                  <th scope="col">Scales</th>
                  <th scope="col">Direction</th>
                  <th scope="col">Position</th>
                </tr>
              </thead>
              <tbody>
                {anomalies.map((row, index) => (
                  <tr key={`${row.regionId}-${row.effectiveRadiusArcsec}-${index}`}>
                    <td>
                      <a href={`/tract/${row.tract}`}>{row.regionId.replace("dp2-tract-", "Tract ")}</a>
                    </td>
                    <td className={styles.numeric}>{row.effectiveRadiusArcsec.toFixed(0)}″</td>
                    <td className={styles.numeric}>{row.empiricalSigma.toFixed(1)}</td>
                    <td className={styles.numeric}>{Math.abs(row.amplitudeNjy).toPrecision(3)} nJy</td>
                    <td className={styles.numeric}>{row.scalesDetected ?? 1}</td>
                    <td className={row.direction === "rubin-excess" ? styles.excess : styles.deficit}>
                      {row.direction === "rubin-excess" ? "Rubin brighter" : "Reference brighter"}
                    </td>
                    <td className={styles.numeric}>
                      <small>
                        {row.sky.raDeg.toFixed(5)}, {row.sky.decDeg.toFixed(5)}
                      </small>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </section>
  );
}

export default DifferenceIndex;
