import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const nextCli = fileURLToPath(import.meta.resolve("next/dist/bin/next"));
const result = spawnSync(process.execPath, [nextCli, "build"], {
  env: { ...process.env, VERCEL: process.env.VERCEL || "local-build" },
  stdio: "inherit",
});

process.exit(result.status ?? 1);
