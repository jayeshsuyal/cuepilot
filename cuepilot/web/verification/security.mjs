import { spawnSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
const mode = process.argv[2];
if (!["code", "node", "all"].includes(mode))
  throw new Error("Choose code, node, or all.");
const directory = resolve(
  "verification/security-reports",
  new Date().toISOString().replaceAll(":", "-"),
);
mkdirSync(directory, { recursive: true, mode: 0o700 });
const summaries = [];
for (const kind of mode === "all" ? ["code", "node"] : [mode]) {
  const args =
    kind === "code"
      ? [
          "code",
          "test",
          ".",
          "--sarif-file-output=" + resolve(directory, "code.sarif"),
        ]
      : [
          "test",
          "--all-projects",
          "--detection-depth=1",
          "--dev",
          "--json-file-output=" + resolve(directory, "dependencies.json"),
        ];
  const result = spawnSync(process.env.CUEPILOT_SNYK_CLI || "snyk", args, {
    encoding: "utf8",
    timeout: 180000,
  });
  const output = `${result.stdout || ""}\n${result.stderr || ""}\n${result.error?.message || ""}`;
  writeFileSync(resolve(directory, kind + ".log"), output, { mode: 0o600 });
  const status =
    result.status === 0
      ? "completed_no_findings"
      : result.status === 1
        ? "completed_findings"
        : "incomplete";
  summaries.push({ kind, args, exitCode: result.status, status });
  console.log(`${kind}: ${status}\n${output.trim()}\n`);
}
writeFileSync(
  resolve(directory, "summary.json"),
  JSON.stringify(summaries, null, 2) + "\n",
  { mode: 0o600 },
);
console.log("Evidence:", directory);
process.exitCode = summaries.some((s) => s.status === "incomplete")
  ? 2
  : summaries.some((s) => s.exitCode !== 0)
    ? 1
    : 0;
