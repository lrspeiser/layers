import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

// HSC PDR2 is the largest gap in this project's optical coverage: 162 regions
// claimed by footprint overlap, zero delivered, because the only thing PDR2
// serves without credentials is HiPS. The acquisition path exists now, and these
// tests exist so that a credentialed fetch cannot start leaking the credential.

const script = readFileSync("pipeline/acquire_hsc_pdr2.py", "utf8");

test("credentials come from the environment, never the command line", () => {
  // A password in argv is readable from shell history and the process table.
  assert.ok(!/add_argument\(\s*["']--password/.test(script), "must not accept --password");
  assert.ok(!/add_argument\(\s*["']--user/.test(script), "must not accept --username");
  assert.match(script, /os\.environ\.get\("HSC_PASSWORD"\)/);
  assert.match(script, /os\.environ\.get\("HSC_USERNAME"\)/);
});

test("no credential is ever written to the manifest", () => {
  assert.match(script, /"serialized":\s*False/);
  // The manifest payload must not interpolate either secret.
  const payloadStart = script.indexOf("payload = {");
  const payload = script.slice(payloadStart);
  assert.ok(!/\bpassword\b(?!["']\s*:)/.test(payload.split("credentials")[0] ?? ""),
    "the payload must not carry the password variable");
});

test("errors are redacted before they are recorded", () => {
  // A urllib error can carry the request URL, and a badly built request can carry
  // the credential in it. Failures are stored in the manifest, so they get scrubbed.
  assert.match(script, /def redact\(/);
  assert.match(script, /redact\(f"\{type\(error\)\.__name__\}: \{error\}", secrets\)/);
});

test("a rejected credential is not retried", () => {
  // Retrying a bad password risks locking the account, and it will not succeed.
  assert.match(script, /error\.code in \(401, 403\)/);
});

test("it states that third parties cannot reproduce the fetch", () => {
  // Honesty about a reproducibility limit that belongs to the data, not the code.
  assert.match(script, /reproducibility|cannot re-run this fetch/i);
  assert.match(script, /sha256/i, "checksums keep the result checkable even so");
});

test("the .env file it reads is gitignored", () => {
  const gitignore = readFileSync(".gitignore", "utf8");
  assert.match(gitignore, /^\.env\*/m, "credentials must not be committable");
});
