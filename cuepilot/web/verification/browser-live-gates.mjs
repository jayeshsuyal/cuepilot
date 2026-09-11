import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { setTimeout as delay } from "node:timers/promises";

// UI-only regression: every API read is synthetic and every write is blocked.
// Requires only an isolated Vite server on 5177; no backend or sponsor is used.
const base = "http://127.0.0.1:5177";
const output = resolve(
  process.env.EVIDENCE_DIR ??
    join(
      dirname(fileURLToPath(import.meta.url)),
      "../../.runtime/browser-live-gates",
      new Date().toISOString().replaceAll(":", "-"),
    ),
);
await mkdir(output, { recursive: true });
const { chromium } = await import(
  pathToFileURL(
    join(
      homedir(),
      ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs",
    ),
  ).href
);
const evidence = {
  timestamp: new Date().toISOString(),
  scope:
    "UI-only synthetic API reads; no backend/sponsor execution and all HTTP writes blocked",
  checks: [],
  writes: [],
  errors: [],
};
const now = new Date().toISOString();
const show = {
  id: "ui-only-verification",
  revision: 2,
  title: "Synthetic preparation-gate verification",
  speakers: [
    {
      id: "maya",
      name: "Maya Chen",
      title: "Opening speaker",
      ready: true,
      presentationAssetId: "slides-maya",
    },
  ],
  assets: [
    {
      id: "slides-maya",
      title: "Synthetic presentation",
      kind: "slide",
      status: "ready",
    },
  ],
};
const stage = {
  revision: 0,
  scene: "holding",
  speakerId: null,
  title: "We'll be right with you",
  subtitle: "CuePilot",
  assetId: null,
  reason: null,
  updatedAt: now,
};
const run = {
  id: "ui-only-live-gate",
  showId: show.id,
  speakerId: "maya",
  executionMode: "live",
  status: "needs_approval",
  notes: "Synthetic UI verification only.",
  plan: {
    id: "synthetic-plan",
    hash: "a".repeat(64),
    recipeId: "speaker-segment-v1",
    recipeVersion: 1,
    showRevision: 2,
    speakerId: "maya",
    cues: ["intro", "presentation", "holding"].map((scene, index) => ({
      index,
      scene,
    })),
    origin: "sponsor",
  },
  nextStep: 0,
  receipts: [],
  traces: [],
  reason: null,
  createdAt: now,
  updatedAt: now,
};
const browser = await chromium.launch({ channel: "chrome", headless: true });
const context = await browser.newContext({
  viewport: { width: 1366, height: 900 },
  serviceWorkers: "block",
});
context.on("page", (page) =>
  page.on("pageerror", (error) => evidence.errors.push(error.message)),
);
await context.route("**/*", async (route) => {
  const request = route.request(),
    url = new URL(request.url());
  if (!["GET", "HEAD", "OPTIONS"].includes(request.method())) {
    evidence.writes.push({ method: request.method(), path: url.pathname });
    return route.abort();
  }
  if (url.origin !== base) return route.abort();
  if (!url.pathname.startsWith("/api/")) return route.continue();
  const value =
    url.pathname === "/api/v1/show"
      ? show
      : url.pathname === "/api/v1/stage"
        ? stage
        : url.pathname === "/api/v1/runs"
          ? [run]
          : url.pathname === `/api/v1/runs/${run.id}`
            ? run
            : null;
  assert.ok(value, `Unexpected API read: ${url.pathname}`);
  return route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(value),
  });
});
const desk = await context.newPage();
async function until(check, message) {
  const deadline = Date.now() + 6000;
  while (Date.now() < deadline) {
    if (await check()) return;
    await delay(80);
  }
  assert.fail(message);
}
function pass(name) {
  evidence.checks.push({ name, result: "passed" });
  console.log(`PASS ${name}`);
}
const approved = () =>
  desk.getByRole("button", { name: "Approve plan", exact: true });
const trace = (status) => ({
  provider: "rocketride",
  operation: "prepare",
  status,
  evidence: { synthetic: true },
});
try {
  await desk.goto(base);
  await desk.getByText("API connected", { exact: true }).waitFor();
  await desk
    .getByText("Finalizing sponsor preparation", { exact: true })
    .waitFor();
  await desk.getByText("Finalizing plan", { exact: true }).waitFor();
  assert.equal(await approved().isDisabled(), true);
  await desk.screenshot({
    path: join(output, "preparation-finalizing.png"),
    fullPage: true,
  });
  pass(
    "A returned live plan without final RocketRide preparation shows Finalizing and disables approval",
  );

  run.traces = [
    {
      provider: "cognee",
      operation: "ingest",
      status: "verified",
      evidence: { synthetic: true },
    },
    trace("blocked"),
  ];
  await until(
    async () => (await desk.locator(".evidence-row").count()) === 2,
    "Unverified trace was not rendered",
  );
  assert.equal(await approved().isDisabled(), true);
  pass(
    "Unrelated verified evidence and an unverified prepare trace do not enable approval",
  );

  run.traces.push(trace("verified"));
  await until(
    async () => !(await approved().isDisabled()),
    "Final verified preparation did not enable approval",
  );
  await desk
    .getByText("Review and approve the plan", { exact: true })
    .waitFor();
  await desk.screenshot({
    path: join(output, "preparation-verified.png"),
    fullPage: true,
  });
  pass("Only the latest verified RocketRide prepare trace enables approval");

  run.traces.push(trace("failed"));
  await until(
    async () => approved().isDisabled(),
    "A later failed prepare trace did not disable approval",
  );
  pass("A newer failed prepare trace supersedes earlier verification");

  run.status = "approved";
  await desk.getByText("Ready to execute live", { exact: true }).waitFor();
  assert.equal(
    await desk
      .getByRole("button", { name: "Execute live sequence", exact: true })
      .count(),
    0,
  );
  run.traces.push(trace("verified"));
  await desk
    .getByRole("button", { name: "Execute live sequence", exact: true })
    .waitFor();
  pass(
    "Execution control also requires final preparation verification; no execution was clicked",
  );

  run.status = "completed";
  run.executionMode = "practice";
  run.plan.origin = "fixture";
  run.plan.showRevision = 1;
  run.nextStep = 3;
  run.receipts = run.plan.cues.map((cue, index) => ({
    ok: true,
    id: `synthetic-${index}`,
    runId: run.id,
    stepIndex: index,
    scene: cue.scene,
    stageRevision: index + 1,
    committedAt: now,
  }));
  await desk.getByText("Segment finished", { exact: true }).waitFor();
  assert.equal(
    await desk
      .getByText("Show changed after planning", { exact: true })
      .count(),
    0,
  );
  assert.equal(
    await desk
      .getByRole("button", { name: "Create recovery plan", exact: true })
      .count(),
    0,
  );
  await desk
    .getByRole("button", { name: "Create live run", exact: true })
    .waitFor();
  await desk.screenshot({
    path: join(output, "historical-completed.png"),
    fullPage: true,
  });
  pass(
    "A completed historical plan at an older revision has no stale-plan warning or recovery label",
  );
  assert.deepEqual(evidence.writes, []);
  assert.deepEqual(evidence.errors, []);
  evidence.result = "passed";
} catch (error) {
  evidence.result = "failed";
  evidence.error = error.stack;
  await desk.screenshot({ path: join(output, "failure.png"), fullPage: true });
  throw error;
} finally {
  await writeFile(
    join(output, "checks.json"),
    JSON.stringify(evidence, null, 2) + "\n",
  );
  await browser.close();
  console.log(`Evidence: ${output}`);
}
