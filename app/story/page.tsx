import type { Metadata } from "next";
import Link from "next/link";
import styles from "./page.module.css";
import SiteNav from "@/components/SiteNav";
import release from "@/public/data/layers/selected-regions/catalogue-release.json";
import threeWay from "@/public/data/layers/selected-regions/three-way-optical-corrected.json";
import segments from "@/public/data/layers/selected-regions/segment-noise-inflation.json";
import epochs from "@/public/data/layers/selected-regions/epoch-separation.json";
import vetting from "@/public/data/layers/selected-regions/difference-vetting.json";

export const metadata: Metadata = {
  title: "Does Rubin see what everyone else sees?",
  description:
    "The same patch of sky from two telescopes, and what it takes to tell a real difference from an instrumental one.",
};

// The worked example. Same Rubin pixels, two reference surveys, two different
// answers -- which is the whole argument for needing more than one reference,
// and it is more convincing shown than described.
const EXAMPLE = {
  tract: 11411,
  legacy: {
    root: "/layer-previews/selected-regions-200/comparisons/dp2-tract-11411",
    label: "Legacy DR10",
    rank: 4,
    fractionAbove5Sigma: 0.032,
    p99: 7.7,
    offSource: 3,
  },
  ps1: {
    root: "/layer-previews/comparisons-ps1/dp2-tract-11411",
    label: "Pan-STARRS DR2",
    rank: 49,
    fractionAbove5Sigma: 0.0068,
    p99: 3.98,
    offSource: 0,
  },
};

export default function StoryPage() {
  const after = threeWay.after as Record<string, { medianScale: number }>;
  const pairs = epochs.pairs as Record<string, { label: string; yearsFromRubin: number }>;

  return (
    <main id="top">
      <SiteNav chip="THE QUESTION" current="/" />

      <div className={styles.page}>
        <header className={styles.hero}>
          <h1>Does Rubin see what everyone else sees?</h1>
          <p className={styles.standfirst}>
            A new telescope&rsquo;s first duty is to agree with the old ones. Below is the same
            patch of sky, measured by Rubin and by two older surveys. The two comparisons
            disagree with each other &mdash; and that is the point.
          </p>
        </header>

        <section className={styles.showcase}>
          <h2 className={styles.showcaseTitle}>Tract {EXAMPLE.tract}, two references</h2>
          {[EXAMPLE.legacy, EXAMPLE.ps1].map((ref) => (
            <figure key={ref.label} className={styles.strip}>
              <figcaption>
                <strong>Rubin vs {ref.label}</strong>
                <span>
                  ranked {ref.rank} of 190 · {(ref.fractionAbove5Sigma * 100).toFixed(2)}% of pixels
                  above 5&sigma; · {ref.offSource} peaks in blank sky
                </span>
              </figcaption>
              <div className={styles.frames}>
                <span>
                  <img src={`${ref.root}/rubin-r.png`} alt={`Rubin r-band image of tract ${EXAMPLE.tract}`} loading="lazy" />
                  <small>Rubin</small>
                </span>
                <span>
                  <img src={`${ref.root}/reference-r.png`} alt={`${ref.label} r-band image of the same field`} loading="lazy" />
                  <small>{ref.label}</small>
                </span>
                <span>
                  <img src={`${ref.root}/difference.png`} alt={`Difference between Rubin and ${ref.label}`} loading="lazy" />
                  <small>difference</small>
                </span>
              </div>
            </figure>
          ))}
          <p className={styles.punch}>
            Same Rubin pixels. Same sky. Against Legacy this field looks like one of the most
            discrepant in the survey; against Pan-STARRS it is unremarkable, and the blank-sky
            peaks vanish entirely. Something genuinely out there would look the same to both.
          </p>
          <Link className={styles.cta} href="/explorer">
            Open the explorer &mdash; blink, overlay and rank all 190 fields &rarr;
          </Link>
        </section>

        <section className={styles.thread}>
          <article className={styles.beat}>
            <h2>The images are a decade apart</h2>
            <p>
              Rubin&rsquo;s effective epoch is <strong>{epochs.rubinEpochJyear}</strong>, fitted
              from Gaia proper motions because the cutouts carry no usable observation date. The
              references are older:
            </p>
            <ul className={styles.gaps}>
              {Object.entries(pairs).map(([id, p]) => (
                <li key={id}>
                  <strong>{p.yearsFromRubin} yr</strong> <span>{p.label}</span>
                </li>
              ))}
            </ul>
            <p>
              Over nine years a star moving 44 milliarcseconds a year shifts half a point-spread
              width. Differencing a source that moved gives a <em>dipole</em> — bright on one
              side, dark on the other. A variable star is simply a different brightness. An
              asteroid is in one image and not the other.
            </p>
            <p className={styles.warn}>
              None of those is &ldquo;Rubin disagrees with Legacy&rdquo;, and all of them look
              exactly like it. The anomaly scanner does not yet test for any of them, so nothing
              in its candidate list should be read as a discovery.
            </p>
          </article>

          <article className={styles.beat}>
            <h2>Where the surveys do agree: Rubin reads faint</h2>
            <p>
              On compact sources &mdash; where none of the above applies &mdash; Rubin measures
              about <strong>8&ndash;10% less light</strong> than three independent surveys, which
              agree with each other to under 2%:
            </p>
            <dl className={styles.ledger}>
              <div>
                <dt>Legacy DR10</dt>
                <dd>{after["legacy-surveys-dr10"].medianScale.toFixed(3)}</dd>
              </div>
              <div>
                <dt>Pan-STARRS DR2</dt>
                <dd>{after["panstarrs-dr2"].medianScale.toFixed(3)}</dd>
              </div>
              <div>
                <dt>HSC PDR2</dt>
                <dd>{after["hsc-ssp-pdr2"].medianScale.toFixed(3)}</dd>
              </div>
            </dl>
            <details className={styles.detail}>
              <summary>Getting there meant finding our own mistake</summary>
              <p>
                Pan-STARRS used to read 1.157 &mdash; Rubin 16% <em>bright</em>, contradicting the
                other two. The error was ours: our conversion of Pan-STARRS pixels was wrong by
                0.244 magnitudes. Corrected against Pan-STARRS&rsquo;s own published brightnesses,
                three-way disagreement fell from{" "}
                {threeWay.medianAgreementBefore.toFixed(3)} to{" "}
                {threeWay.medianAgreementAfter.toFixed(3)}.
              </p>
              <p>
                The first attempt at that measurement gave 0.665 magnitudes with a scatter of
                0.5&ndash;0.7 &mdash; ten times too large to measure a zero point. Restricted to
                bright isolated stars it gave 0.244. The first number was not a rougher version of
                the second; it was a different quantity that looked like an answer.
              </p>
            </details>
          </article>

          <article className={styles.beat}>
            <h2>Every error bar was half what it should be</h2>
            <p>
              Uncertainties assume each pixel&rsquo;s noise is independent of its neighbours.
              Real images have been shifted and stacked, which smears noise across pixels.
              Measured by dropping {segments.segmentsMeasured.toLocaleString()} real source
              outlines onto blank sky, the true uncertainty is{" "}
              <strong>{segments.overall.medianErrorBarUnderstatedBy.toFixed(2)}&times;</strong>{" "}
              larger. The catalogue is corrected and ships the factor as a column.
            </p>
          </article>

          <article className={styles.beat}>
            <h2>What is still unexplained</h2>
            <p>
              Field to field the ratio wanders more than any of this accounts for, and the wander
              is common to all three references &mdash; so it is not one survey&rsquo;s
              calibration. Galaxies carry a size-dependent bias of unknown cause. The colour
              transfer needed to compare resolved galaxies has never passed its own quality check.
            </p>
            <p className={styles.muted}>
              The scanner refuses fields whose calibration it cannot verify: {vetting.counts.scanned}{" "}
              scanned, <strong>{vetting.counts.refused} refused</strong>. Tract {EXAMPLE.tract}{" "}
              above is one of the refused &mdash; it ranks 4th and was thrown out on 15 matched
              stars against a floor of 20.
            </p>
          </article>
        </section>

        <footer className={styles.foot}>
          <p>
            <strong>No astrophysical claim stands here.</strong> {release.rows.toLocaleString()}{" "}
            sources are published for anyone to check &mdash; download them, query them by
            position, or look at the images yourself.
          </p>
          <nav className={styles.exits}>
            <Link href="/explorer">Explore the differences</Link>
            <Link href="/data">Download &amp; query</Link>
            <Link href="/coverage">Where the data is</Link>
            <Link href="/differences">All nine operators</Link>
            <Link href="/goals">What was attempted</Link>
          </nav>
        </footer>
      </div>
    </main>
  );
}
