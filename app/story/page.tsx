import type { Metadata } from "next";
import Link from "next/link";
import styles from "./page.module.css";
import release from "@/public/data/layers/selected-regions/catalogue-release.json";
import threeWay from "@/public/data/layers/selected-regions/three-way-optical-corrected.json";
import segments from "@/public/data/layers/selected-regions/segment-noise-inflation.json";
import bias from "@/public/data/layers/selected-regions/aperture-bias.json";
import vetting from "@/public/data/layers/selected-regions/difference-vetting.json";
import scorecard from "@/public/data/layers/goal-scorecard.json";

export const metadata: Metadata = {
  title: "Does Rubin see what everyone else sees?",
  description:
    "Comparing the Rubin Observatory's deep images against three older surveys on identical pixels — what agrees, what doesn't, and what we still can't explain.",
};

const pct = (x: number) => `${(x * 100).toFixed(1)}%`;

export default function StoryPage() {
  const after = threeWay.after as Record<string, { medianScale: number; fieldSpreadDex: number }>;
  const legacy = after["legacy-surveys-dr10"];
  const ps1 = after["panstarrs-dr2"];
  const hsc = after["hsc-ssp-pdr2"];
  const refused = vetting.counts.refused;
  const scanned = vetting.counts.scanned;

  return (
    <main className={styles.page}>
      <header className={styles.hero}>
        <p className={styles.eyebrow}>Rubin Light Atlas</p>
        <h1>Does Rubin see what everyone else sees?</h1>
        <p className={styles.standfirst}>
          A new telescope&rsquo;s first duty is to agree with the old ones. Where it doesn&rsquo;t,
          either the telescope is wrong, the old surveys are wrong, or something is actually out
          there. Telling those three apart is most of the work &mdash; and it is nearly all of what
          this page is about.
        </p>
        <p className={styles.meta}>
          {release.rows.toLocaleString()} sources · {release.regions} regions · four reference
          surveys · every number below is measured, and the ones that turned out wrong are still
          here, marked
        </p>
      </header>

      <section className={styles.thread}>
        <article className={styles.beat}>
          <h2>1. The honest way to compare two telescopes</h2>
          <p>
            The obvious approach is to take each survey&rsquo;s published catalogue and match
            objects by position. It is also the wrong one: every mismatch becomes a fake
            difference, and mismatches are common exactly where the sky is crowded and interesting.
          </p>
          <p>
            So nothing here is cross-matched. Rubin and the reference are put on the same pixel
            grid, blurred to the same sharpness, and measured through the <em>same</em> apertures.
            A row in the catalogue carries two brightnesses for one patch of sky, not two objects
            somebody decided were the same.
          </p>
          <details className={styles.detail}>
            <summary>Why that choice costs something</summary>
            <p>
              Matching apertures means accepting the worse seeing of the two, and it means one
              image has to be blurred to match the other. Which one gets blurred is decided per
              field by whichever direction leaves less residual &mdash; a bookkeeping detail that{" "}
              <strong>should not</strong> affect the photometry.
            </p>
            <p>
              It does. Extended sources measure fainter in Rubin, and about three times more so
              when Rubin is the blurred frame &mdash; a difference of{" "}
              {bias.groups["rubin-convolved"].medianSizeBias.toFixed(3)} against{" "}
              {bias.groups["reference-convolved"].medianSizeBias.toFixed(3)} across{" "}
              {Object.values(bias.groups).reduce((n, g) => n + g.regions, 0)} fields, at{" "}
              p&nbsp;=&nbsp;{bias.kruskalWallisP?.toExponential(1)}. Nothing on the sky knows which
              frame we chose to blur, so part of that is ours. Two candidate explanations were
              tested and both failed.
            </p>
          </details>
        </article>

        <article className={styles.beat}>
          <h2>2. Rubin reads faint &mdash; and three surveys agree</h2>
          <p>
            On compact sources, Rubin measures about <strong>8&ndash;10% less light</strong> than
            three older surveys. Different telescopes, different filters, different calibration
            lineages, all landing in the same place:
          </p>
          <dl className={styles.ledger}>
            <div>
              <dt>Legacy DR10</dt>
              <dd>{legacy.medianScale.toFixed(3)}</dd>
            </div>
            <div>
              <dt>Pan-STARRS DR2</dt>
              <dd>{ps1.medianScale.toFixed(3)}</dd>
            </div>
            <div>
              <dt>HSC PDR2</dt>
              <dd>{hsc.medianScale.toFixed(3)}</dd>
            </div>
          </dl>
          <p className={styles.aside}>
            A value of 1.000 would mean perfect agreement. These three agree with each other to{" "}
            {threeWay.medianAgreementAfter.toFixed(3)} &mdash; under 2% &mdash; while all saying
            Rubin is the faint one.
          </p>
          <details className={styles.detail}>
            <summary>This is a calibration result, not missing light</summary>
            <p>
              A consistent brightness offset between surveys is ordinary. Filters differ, zero
              points are set by different standards, and 8% is well within the range where those
              explanations live. It becomes interesting only if it survives all of them, and that
              test has not been done.
            </p>
          </details>
        </article>

        <article className={styles.beat}>
          <h2>3. Getting there meant finding our own mistake</h2>
          <p>
            Until recently that table looked quite different. Pan-STARRS said Rubin was 16%{" "}
            <em>bright</em> while the other two said faint &mdash; one survey flatly contradicting
            the rest.
          </p>
          <p>
            The tempting reading is that Pan-STARRS was the odd one out. The correct reading was
            that <strong>our conversion of Pan-STARRS was wrong by 0.244 magnitudes</strong>. Tested
            against Pan-STARRS&rsquo;s own published brightnesses for the same stars, and corrected,
            it moved from 1.157 to {ps1.medianScale.toFixed(3)} and the three-way disagreement fell
            from {threeWay.medianAgreementBefore.toFixed(3)} to{" "}
            {threeWay.medianAgreementAfter.toFixed(3)}.
          </p>
          <details className={styles.detail}>
            <summary>The first attempt got a different answer, and it was the wrong question</summary>
            <p>
              Measured across every star the software could find, the error came out at 0.665
              magnitudes with a scatter of 0.5&ndash;0.7. That scatter is ten times too large to
              measure a zero point, and the number was biased: at the faint end only the upward
              noise excursions get detected at all.
            </p>
            <p>
              Restricted to bright, isolated, unsaturated stars, the scatter fell to 0.13&ndash;0.33
              and the answer to 0.244. The first number was not a rougher version of the second. It
              was a different quantity that happened to look like an answer.
            </p>
          </details>
        </article>

        <article className={styles.beat}>
          <h2>4. Every error bar was about half what it should be</h2>
          <p>
            Software computes the uncertainty on a brightness by assuming each pixel&rsquo;s noise is
            independent of its neighbours. In real astronomical images it is not: the pictures have
            been shifted, rotated and stacked, and that smears noise across neighbouring pixels.
          </p>
          <p>
            Measured directly &mdash; by dropping {segments.segmentsMeasured.toLocaleString()} real
            source outlines onto blank sky and watching how much the totals actually wobble &mdash;
            the true uncertainty is <strong>
              {segments.overall.medianErrorBarUnderstatedBy.toFixed(2)}&times;
            </strong>{" "}
            larger than the formula says. The published catalogue has been corrected, and ships the
            correction factor as its own column so the original values are recoverable.
          </p>
        </article>

        <article className={styles.beat}>
          <h2>5. What we cannot explain</h2>
          <p>
            Field to field, the Rubin-versus-reference ratio wanders by more than any of this
            accounts for. The natural suspect was one survey&rsquo;s calibration &mdash; but the
            wander is <em>common to all three references</em> on the same sky, which rules that out.
            It belongs to Rubin or to the sky, and it is still open.
          </p>
          <p>
            Extended objects &mdash; galaxies, most of the catalogue &mdash; carry a size-dependent
            bias whose cause is unknown. And the colour transfer that would let us compare resolved
            galaxies quantitatively has never passed its own quality check, which is why no
            astrophysical claim is made anywhere on this site.
          </p>
          <details className={styles.detail}>
            <summary>How much of the data is unvetted, in numbers</summary>
            <p>
              The anomaly scanner refuses to look for interesting residuals in a field whose
              calibration it cannot verify. Across region-and-reference pairs it scanned{" "}
              {scanned} and <strong>refused {refused}</strong>, {vetting.counts.byReason[
                "flux transfer not corroborated"
              ]}{" "}
              of those because the brightness transfer could not be corroborated from enough
              matched stars.
            </p>
            <p>
              This matters when reading the difference explorer, which ranks fields by raw
              disagreement and deliberately does not filter. A field can sit near the top of that
              ranking and have been thrown out by the scanner. Both facts are true; only one used
              to be visible.
            </p>
          </details>
        </article>

        <article className={styles.beat}>
          <h2>6. What you can actually do with it</h2>
          <p>
            The catalogue is public: {release.rows.toLocaleString()} sources, downloadable as
            Parquet or VOTable, and queryable by position from TOPCAT, Aladin or three lines of{" "}
            <code>pyvo</code>. Cut on <code>departure_significance</code>, and cut on the compact
            half of each field.
          </p>
          <nav className={styles.exits}>
            <Link href="/data">Download &amp; query</Link>
            <Link href="/explorer">See where images disagree</Link>
            <Link href="/coverage">Where the data actually is</Link>
            <Link href="/goals">What was attempted vs. what exists</Link>
          </nav>
        </article>
      </section>

      <footer className={styles.foot}>
        <p>
          <strong>No astrophysical claim stands here.</strong> {scorecard.policy.note}
        </p>
        <p className={styles.muted}>
          Everything above is reproducible from the scripts in <code>pipeline/</code>. Rubin pixels
          need data rights; every other input is public, and the manifests carry checksums.
        </p>
      </footer>
    </main>
  );
}
