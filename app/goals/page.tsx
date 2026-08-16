import type { Metadata } from "next";
import Link from "next/link";
import styles from "./page.module.css";
import scorecard from "@/public/data/layers/goal-scorecard.json";
// Slim companion: the full file carries a per-region block.
import blockers from "@/public/data/layers/selected-regions/blocker-reassessment-slim.json";

export const metadata: Metadata = {
  title: "What was asked, what exists, what was delivered",
  description:
    "Every goal against the archive ceiling that bounds it, so a shortfall can be told apart from a target that was never reachable.",
};

type Goal = {
  id: string;
  name: string;
  statedTarget: number | null;
  archiveCeiling: number | null;
  ceilingBasis: string | null;
  ceilingIsIndependentOfResult: boolean | null;
  delivered: number | null;
  unit: string;
  targetExceedsCeilingBy: number | null;
  fractionOfCeiling: number | null;
  status: string;
  evidence: string | null;
  note: string | null;
};

const STATUS_LABEL: Record<string, string> = {
  met: "met",
  "at-ceiling": "at ceiling",
  "below-ceiling": "below ceiling",
};

export default function GoalsPage() {
  const goals = scorecard.goals as Goal[];
  const counts = scorecard.counts;
  const policy = scorecard.policy;

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
          <Link href="/data">Data access</Link>
          <Link href="/explorer">Difference explorer</Link>
          <Link href="/differences">Operators</Link>
        </nav>
        <span className="release-chip">GOALS</span>
      </header>

      <section className={styles.page}>
        <header className={styles.intro}>
          <span>COVERAGE ACCOUNTING</span>
          <h1>
            {counts.targetsExceedingArchiveCeiling} of the {goals.length} targets asked for more
            than the archives hold
          </h1>
          <p>
            Each goal below carries three numbers: what was asked for, what the archives can
            actually supply, and what was delivered. They are kept apart deliberately. A target
            written from footprint overlap counts sky, not data, and several of these were
            unreachable before a single pixel was fetched — so falling short of one is a different
            fact from falling short of what exists.
          </p>
          <dl className={styles.headline}>
            <div>
              <dt>met</dt>
              <dd>{counts.met}</dd>
            </div>
            <div>
              <dt>at ceiling</dt>
              <dd>{counts.atCeiling}</dd>
            </div>
            <div>
              <dt>below ceiling</dt>
              <dd>{counts.belowCeiling}</dd>
            </div>
          </dl>
        </header>

        <ol className={styles.goals}>
          {goals.map((goal) => {
            const unreachable = (goal.targetExceedsCeilingBy ?? 0) > 0;
            return (
              <li key={goal.id} className={styles.goal} data-status={goal.status}>
                <div className={styles.head}>
                  <h2>
                    <span className={styles.gid}>{goal.id}</span> {goal.name}
                  </h2>
                  <span className={styles.status} data-status={goal.status}>
                    {STATUS_LABEL[goal.status] ?? goal.status}
                  </span>
                </div>

                <div className={styles.numbers}>
                  <div>
                    <dt>asked for</dt>
                    <dd>{goal.statedTarget ?? "—"}</dd>
                  </div>
                  <div data-tone={unreachable ? "ceiling" : undefined}>
                    <dt>archives hold</dt>
                    <dd>{goal.archiveCeiling ?? "not bounded"}</dd>
                  </div>
                  <div>
                    <dt>delivered</dt>
                    <dd>{goal.delivered ?? "—"}</dd>
                  </div>
                  <div>
                    <dt>unit</dt>
                    <dd className={styles.unit}>{goal.unit}</dd>
                  </div>
                </div>

                {unreachable && (
                  <p className={styles.unreachable}>
                    The target exceeded what the archives hold by {goal.targetExceedsCeilingBy}.
                    That part was never reachable.
                  </p>
                )}

                {goal.ceilingBasis && (
                  <p className={styles.basis} data-independent={goal.ceilingIsIndependentOfResult}>
                    Ceiling {goal.ceilingBasis}.
                    {goal.ceilingIsIndependentOfResult === false &&
                      " Self-derived: it is the delivered value itself, so it cannot be used to argue the result is complete."}
                  </p>
                )}

                {goal.note && <p className={styles.note}>{goal.note}</p>}

                {goal.evidence && (
                  <p className={styles.evidence}>
                    <a href={`/data/layers/${goal.evidence}`}>{goal.evidence}</a>
                  </p>
                )}
              </li>
            );
          })}
        </ol>

        <section className={styles.policy}>
          <h2>What actually stands between here and a comparison</h2>
          <p>
            No comparison has cleared every gate, and the count of what blocks them was written
            before two of the blockers were worked on. Recomputed against current evidence, per
            region:
          </p>
          <ul className={styles.blockers}>
            {Object.entries(blockers.blockersRemaining as Record<string, number>)
              .sort((a, b) => b[1] - a[1])
              .map(([name, count]) => (
                <li key={name}>
                  <strong>{count}</strong> <span>{name}</span>
                </li>
              ))}
          </ul>
          <p className={styles.muted}>
            {blockers.staleBlockersClearedByNewEvidence["bandpass transfer"]} regions now carry a
            fitted per-region colour term that the reconciliation manifest still counts as
            blocked. Resampling covariance is the one blocker on every region: it has been
            measured everywhere and applied nowhere, and a systematic that is known but
            uncorrected still blocks a quantitative claim.
          </p>
        </section>

        <footer className={styles.policy}>
          <h2>What none of this means</h2>
          <p>{policy.note}</p>
          <p className={styles.muted}>
            Comparison-ready products: {policy.comparisonReadyProducts}. Astrophysical claims
            standing: {policy.astrophysicalClaimsStanding}. A goal reaching its ceiling is a
            statement about acquisition and measurement, never about a result being publishable.
          </p>
        </footer>
      </section>
    </main>
  );
}
