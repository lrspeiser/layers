import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

// The extended-source transfer gates every quantitative claim in this project:
// it is why bandpass transfer blocks all 190 regions and comparisonReady is 0.
// These tests keep the diagnosis of *which* threshold it fails from decaying
// back into the undifferentiated "the transfer fails".

const read = (path) => JSON.parse(readFileSync(path, "utf8"));
const diagnosis = read("public/data/layers/selected-regions/extended-transfer-diagnosis.json");

test("every audited target is still failing", () => {
  assert.ok(diagnosis.targetsAudited >= 3);
  for (const target of diagnosis.targets) {
    assert.ok(
      Object.keys(target.breaches).length > 0,
      `${target.objectId} breaches nothing, so the blocker should have moved`,
    );
  }
});

test("the failure is separated into offset versus scatter", () => {
  // Failing on scatter means the colour model does not work on resolved light.
  // Failing on median with scatter inside tolerance means it works very well and
  // everything is displaced by a constant. Those implicate different causes and
  // must not be collapsed into one verdict.
  const clean = diagnosis.targets.find((t) => t.objectId === "ugc00891");
  assert.ok(clean, "expected the full-colour-support target");
  assert.ok(clean.within.robustResidualScatterMag, "its scatter should be inside tolerance");
  assert.ok(clean.breaches.medianAbsoluteResidualMag, "its median should breach");
  assert.ok(
    clean.within.robustResidualScatterMag.value < clean.within.robustResidualScatterMag.limit / 2,
    "scatter should be comfortably inside, not marginally",
  );
});

test("the n=1 caution travels with the finding", () => {
  // Two small samples reversed against their full runs this session. A single
  // target is smaller than either, and the manifest has to say so wherever the
  // finding is read.
  assert.match(diagnosis.caution, /n=1|one target/i);
  assert.match(diagnosis.caution, /lead/i);
  assert.deepEqual(diagnosis.offsetNotScatter, ["ugc00891"]);
});

test("it is reproducible without restricted pixels", () => {
  const script = readFileSync("pipeline/diagnose_extended_transfer.py", "utf8");
  assert.match(diagnosis.reproduce, /diagnose_extended_transfer/);
  assert.ok(!/RUBIN_RSP_TOKEN/.test(script), "must not need credentials to re-check");
});
