import test from "node:test";
import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const readJson = async (...parts) => JSON.parse(await readFile(join(root, ...parts), "utf8"));

// The MOC is this project's answer to "where do you actually have data" — the
// question it got wrong seven times by trusting declared footprints. The one
// thing it must never do is repeat that mistake in the other direction, by
// reporting a survey as covering nothing when it simply was not measured.
test("the coverage MOC never reports unmeasured as zero", async () => {
  const moc = await readJson("public", "data", "layers", "selected-regions", "coverage-moc.json");

  for (const survey of moc.surveys) {
    assert.equal(typeof survey.servedMeasured, "boolean");
    if (survey.servedMeasured) {
      // A measured row must name the manifest that proves it and carry a real count.
      assert.ok(survey.servedEvidence, `${survey.surveyId} claims measurement with no evidence`);
      assert.ok(survey.servedRegions > 0, `${survey.surveyId} measured but served nothing`);
      assert.ok(survey.servedAreaSqDeg > 0);
    } else {
      // An unmeasured row must state nothing about served coverage at all.
      assert.equal(survey.servedRegions, null, `${survey.surveyId} reports served with no measurement`);
      assert.equal(survey.servedAreaSqDeg, null);
      assert.ok(survey.note, `${survey.surveyId} is unmeasured without saying so`);
    }
  }

  // A wired manifest that yields nothing is broken wiring, and is reported as
  // such rather than becoming a zero.
  assert.equal(moc.counts.wiredButYieldedNoRegions, moc.wiredButYieldedNoRegions.length);
});

test("served coverage never exceeds what was claimed, and the files exist", async () => {
  const moc = await readJson("public", "data", "layers", "selected-regions", "coverage-moc.json");
  let anyGap = false;

  for (const survey of moc.surveys) {
    if (survey.claimedMoc) await access(join(root, "public", survey.claimedMoc.slice(1)));
    if (!survey.servedMeasured) continue;
    await access(join(root, "public", survey.servedMoc.slice(1)));

    // Served is a subset of claimed by construction; the reverse would mean the
    // planner missed sky an archive actually covered.
    assert.ok(
      survey.servedRegions <= survey.claimedRegions,
      `${survey.surveyId} served more regions than were claimed`,
    );
    assert.ok(survey.servedAreaSqDeg <= survey.claimedAreaSqDeg + 1e-6);
    if (survey.regionsClaimedWithoutPixels > 0) anyGap = true;
  }

  // If claimed and served ever agreed exactly everywhere, the distinction would
  // not be worth publishing. It does not.
  assert.ok(anyGap, "no survey shows a claimed-minus-served gap");
  assert.ok(moc.counts.regionsClaimedWithoutPixels > 0);
});
