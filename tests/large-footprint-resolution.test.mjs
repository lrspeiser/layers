import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

function readJson(relativePath) {
  return JSON.parse(fs.readFileSync(path.join(root, relativePath), "utf8"));
}

test("large release footprints are scoped, checksummed, and redacted", () => {
  const artifactPath = path.join(root, "public/data/coverage/large-footprint-resolution.json");
  const artifactText = fs.readFileSync(artifactPath, "utf8");
  const artifact = JSON.parse(artifactText);
  const validation = readJson("public/data/coverage/large-footprint-resolution-validation.json");

  assert.equal(validation.passed, true);
  assert.equal(validation.checkCount, 8);
  assert.deepEqual(
    artifact.resolved.map((item) => item.surveyId).sort(),
    ["act-dr6", "des-y3-lensing", "sdss-dr19", "spt-3g"],
  );
  assert.deepEqual(
    artifact.unresolved.map((item) => item.surveyId).sort(),
    ["hsc-lensing", "hsc-ssp-pdr3", "kids-1000-lensing"],
  );
  assert.equal(artifact.rubinIndex.tractCount, 2191);

  for (const product of artifact.resolved) {
    assert.equal(product.confirmedRubinTractIds.length, product.confirmedRubinTractCount);
    assert.ok(product.confirmedRubinTractIds.every((tract) => Number.isInteger(tract)));
    const mocPath = path.join(root, "public", product.moc.href.replace(/^\//, ""));
    const payload = fs.readFileSync(mocPath);
    assert.equal(payload.byteLength, product.moc.bytes);
    assert.equal(crypto.createHash("sha256").update(payload).digest("hex"), product.moc.sha256);
  }

  const hscGrid = artifact.unresolved.find((item) => item.surveyId === "hsc-ssp-pdr3");
  const hscLensing = artifact.unresolved.find((item) => item.surveyId === "hsc-lensing");
  assert.equal(hscGrid.officialGridIsProductFootprint, false);
  assert.ok(hscGrid.auditedGrid.areaSqDeg > 2000);
  assert.equal(hscLensing.publicReleaseAvailable, false);
  assert.equal(fs.existsSync(path.join(root, "public/data/coverage/mocs-large/hsc-ssp-pdr3.moc.fits")), false);

  const sdss = artifact.resolved.find((item) => item.surveyId === "sdss-dr19");
  const des = artifact.resolved.find((item) => item.surveyId === "des-y3-lensing");
  assert.equal(sdss.source.publisherSha1, sdss.source.computedSha1);
  assert.equal(des.source.archive.publisherMd5, des.source.archive.computedMd5);
  assert.doesNotMatch(artifactText, /Bearer\s+|token=|[A-Za-z]:\\|pipeline\/results/i);
});
