import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { setTimeout as delay } from "node:timers/promises";

// Use a fresh, isolated API behind Vite. Never point this at the operator's desk.
// Example: BASE_URL=http://127.0.0.1:5177 node verification/browser-demo.mjs
// Vite needs CUEPILOT_API_URL=http://127.0.0.1:8907 and its operator token.
// --layout-only performs a read-only follow-up against existing isolated runs.
const layoutOnly = process.argv.includes("--layout-only");
const base = new URL(process.env.BASE_URL ?? "http://127.0.0.1:5177");
assert.ok(["127.0.0.1", "localhost"].includes(base.hostname));
assert.equal(base.protocol, "http:");
assert.ok(
  base.port && !["5173", "8787", "8788"].includes(base.port),
  "Use an isolated frontend port, never the active demo services.",
);
const here = dirname(fileURLToPath(import.meta.url));
const output = resolve(
  process.env.EVIDENCE_DIR ??
    join(
      here,
      "../../.runtime/browser-demo",
      new Date().toISOString().replaceAll(":", "-"),
    ),
);
const runtime =
  process.env.PLAYWRIGHT_MODULE ??
  join(
    homedir(),
    ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs",
  );
const { chromium } = await import(pathToFileURL(runtime).href);
const evidence = {
  timestamp: new Date().toISOString(),
  baseUrl: base.origin,
  scope: layoutOnly
    ? "Read-only Chrome layout follow-up against existing isolated practice runs"
    : "Chrome browser regression against an isolated API; practice execution only",
  checks: [],
  mutations: [],
  violations: [],
  browserErrors: [],
};
await mkdir(output, { recursive: true });

async function read(path) {
  const response = await fetch(new URL(`/api/v1${path}`, base), {
    signal: AbortSignal.timeout(10000),
  });
  assert.equal(response.status, 200, `GET ${path} failed`);
  return response.json();
}
async function until(check, message, timeout = 12000) {
  const deadline = Date.now() + timeout;
  do {
    if (await check()) return;
    await delay(100);
  } while (Date.now() < deadline);
  assert.fail(message);
}
function pass(name, detail = {}) {
  evidence.checks.push({ name, result: "passed", detail });
  console.log(`PASS ${name}`);
}

// Empty history is an additional check that this is a fresh verification API.
const initialRuns = await read("/runs");
if (!layoutOnly)
  assert.deepEqual(
    initialRuns,
    [],
    "Start with a fresh isolated SQLite database.",
  );
const initialShow = await read("/show");
const initialStage = await read("/stage");
const browser = await chromium.launch({ channel: "chrome", headless: true });
const context = await browser.newContext({
  viewport: { width: 1512, height: 1100 },
  serviceWorkers: "block",
});
let desk;
let stagePage;

// A browser action cannot accidentally create/execute a live sponsor run.
await context.route("**/*", async (route) => {
  const request = route.request();
  const url = new URL(request.url());
  if (!["http:", "https:"].includes(url.protocol)) return route.continue();
  if (url.origin !== base.origin) {
    evidence.violations.push(
      `External request blocked: ${url.origin}${url.pathname}`,
    );
    return route.abort("blockedbyclient");
  }
  if (!["GET", "HEAD", "OPTIONS"].includes(request.method())) {
    let body;
    try {
      body = request.postDataJSON();
    } catch {
      body = null;
    }
    const allowed =
      !layoutOnly &&
      request.method() === "POST" &&
      ((url.pathname === "/api/v1/runs" &&
        body?.executionMode === "practice") ||
        /^\/api\/v1\/runs\/[^/]+\/(approve|advance)$/.test(url.pathname));
    evidence.mutations.push({
      method: request.method(),
      path: url.pathname,
      executionMode: body?.executionMode,
    });
    if (!allowed) {
      evidence.violations.push(
        `Unexpected mutation blocked: ${request.method()} ${url.pathname}`,
      );
      return route.abort("blockedbyclient");
    }
  }
  return route.continue();
});
context.on("page", (page) =>
  page.on("pageerror", (error) => evidence.browserErrors.push(error.message)),
);

async function clickWrite(button, endpoint) {
  const responsePromise = desk.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname === `/api/v1${endpoint}`,
  );
  await button.click();
  const response = await responsePromise;
  assert.ok(response.ok(), `${endpoint}: HTTP ${response.status()}`);
  return response.json();
}
async function assertProgram(page, stage) {
  const program = page.getByRole("region", {
    name: "Program output",
    exact: true,
  });
  await program
    .getByRole("heading", { name: stage.title, exact: true })
    .waitFor();
  await until(
    async () =>
      (await program.getAttribute("class"))
        ?.split(/\s+/)
        .includes(`scene-${stage.scene}`),
    `Program did not render ${stage.scene}`,
  );
  assert.equal(
    await program.locator(".program-subtitle").innerText(),
    stage.subtitle,
  );
  assert.equal(
    await program.getByText("Connection lost", { exact: true }).count(),
    0,
  );
}

try {
  desk = await context.newPage();
  await desk.goto(base.href);
  await desk.getByText("API connected", { exact: true }).waitFor();
  await desk.getByLabel("Data source", { exact: true }).selectOption("api");
  await desk.getByRole("radio", { name: "Practice", exact: true }).check();
  assert.equal(
    await desk.getByLabel("Data source", { exact: true }).inputValue(),
    "api",
  );
  assert.equal(
    await desk
      .getByRole("radio", { name: "Practice", exact: true })
      .isChecked(),
    true,
  );
  const popup = context.waitForEvent("page");
  await desk.getByRole("link", { name: "Open stage", exact: true }).click();
  stagePage = await popup;
  await stagePage.setViewportSize({ width: 1366, height: 768 });
  await stagePage.waitForURL(new URL("/stage", base).href);
  await stagePage
    .getByRole("region", { name: "Program output", exact: true })
    .waitFor();
  assert.match(await stagePage.title(), /CuePilot/);
  pass(
    "Connected Local API and explicit Practice selection; separate stage tab opened",
  );

  if (!layoutOnly) {
    const run = await clickWrite(
      desk.getByRole("button", { name: "Create practice run", exact: true }),
      "/runs",
    );
    assert.equal(run.executionMode, "practice");
    assert.equal(run.plan.origin, "fixture");
    assert.equal(run.status, "needs_approval");
    const approved = await clickWrite(
      desk.getByRole("button", { name: "Approve plan", exact: true }),
      `/runs/${run.id}/approve`,
    );
    assert.equal(approved.status, "approved");
    pass(
      "Created a backend practice plan and explicitly approved its exact hash",
      { runId: run.id },
    );

    let priorRevision = initialStage.revision;
    const scenes = [
      ["intro", "Introduce speaker"],
      ["presentation", "Bring up presentation"],
      ["holding", "Return to holding"],
    ];
    for (const [index, [scene, label]] of scenes.entries()) {
      const receipt = await clickWrite(
        desk.getByRole("button", { name: label, exact: true }),
        `/runs/${run.id}/advance`,
      );
      const [stage, show, current] = await Promise.all([
        read("/stage"),
        read("/show"),
        read(`/runs/${run.id}`),
      ]);
      assert.equal(receipt.scene, scene);
      assert.equal(stage.scene, scene);
      assert.equal(current.receipts.length, index + 1);
      assert.equal(current.nextStep, index + 1);
      assert.equal(stage.revision, priorRevision + 1);
      assert.equal(receipt.stageRevision, stage.revision);
      assert.equal(
        show.revision,
        initialShow.revision,
        "Cues must not change show configuration revision",
      );
      await Promise.all([
        assertProgram(desk, stage),
        assertProgram(stagePage, stage),
      ]);
      await until(
        async () =>
          new RegExp(`\\b${index + 1}\\s*\\/\\s*3\\b`).test(
            await desk.locator(".cue-total").innerText(),
          ),
        `Accepted cue count did not reach ${index + 1} / 3`,
      );
      await Promise.all([
        desk.screenshot({
          path: join(output, `${index + 1}-${scene}-desk.png`),
          fullPage: true,
        }),
        stagePage.screenshot({
          path: join(output, `${index + 1}-${scene}-stage.png`),
        }),
      ]);
      pass(`${scene}: preview and separate stage agree with API`, {
        title: stage.title,
        receipts: current.receipts.length,
        showRevision: show.revision,
        stageRevision: stage.revision,
      });
      priorRevision = stage.revision;
    }
    const completed = await read(`/runs/${run.id}`);
    assert.equal(completed.status, "completed");
    assert.deepEqual(
      completed.receipts.map((receipt) => receipt.scene),
      ["intro", "presentation", "holding"],
    );
    assert.ok(
      completed.traces.every((entry) => entry.status === "fixture"),
      "Practice must not claim live sponsor execution",
    );
    await desk.getByText("Segment finished", { exact: true }).waitFor();
    for (const [, label] of scenes) {
      assert.equal(
        await desk.getByRole("button", { name: label, exact: true }).count(),
        0,
        "Completed segment must not offer another cue",
      );
    }
    pass(
      "Holding clearly finishes the segment with three accepted receipts and no next-cue control",
    );

    // A second unapproved practice plan makes history selection meaningful.
    const nextRun = await clickWrite(
      desk.getByRole("button", { name: "Create practice run", exact: true }),
      "/runs",
    );
    assert.notEqual(nextRun.id, run.id);
    const beforeHistoryStage = await read("/stage");
    const beforeHistoryWrites = evidence.mutations.length;
    await desk.getByLabel("Run history", { exact: true }).selectOption(run.id);
    await desk.getByText("Segment finished", { exact: true }).waitFor();
    assert.deepEqual(await read("/stage"), beforeHistoryStage);
    assert.equal(
      evidence.mutations.length,
      beforeHistoryWrites,
      "Selecting history must be read-only",
    );
    await assertProgram(stagePage, beforeHistoryStage);
    pass(
      "Historical run selection is read-only and preserves the global stage",
    );

    await desk.getByRole("radio", { name: "Live", exact: true }).check();
    const beforeReloadWrites = evidence.mutations.length;
    const beforeReload = await Promise.all([
      read("/runs"),
      read("/stage"),
      read("/show"),
    ]);
    await desk.reload();
    await desk.getByText("API connected", { exact: true }).waitFor();
    await until(
      async () =>
        desk.getByRole("radio", { name: "Live", exact: true }).isChecked(),
      "Live selection did not persist across reload",
    );
    // Observe several poll cycles: loading must never create, approve or execute.
    await delay(1600);
    assert.equal(
      evidence.mutations.length,
      beforeReloadWrites,
      "Reload sent a mutation",
    );
    assert.deepEqual(
      await Promise.all([read("/runs"), read("/stage"), read("/show")]),
      beforeReload,
    );
    assert.equal(
      await desk.getByLabel("Data source", { exact: true }).inputValue(),
      "api",
    );
    pass(
      "Reload preserves Live selection without creating, approving or executing a run",
    );
  } else {
    const completedRun = initialRuns.find((run) => run.status === "completed");
    assert.ok(
      completedRun,
      "Layout follow-up needs an existing completed practice run",
    );
    await desk
      .getByLabel("Run history", { exact: true })
      .selectOption(completedRun.id);
    await desk.getByText("Segment finished", { exact: true }).waitFor();
    await Promise.all([
      assertProgram(desk, initialStage),
      assertProgram(stagePage, initialStage),
    ]);
    await stagePage.screenshot({
      path: join(output, "projector-1366x768.png"),
    });
    const stageBounds = await stagePage.evaluate(() => ({
      width: innerWidth,
      height: innerHeight,
      scrollWidth: document.documentElement.scrollWidth,
      scrollHeight: document.documentElement.scrollHeight,
      title: (() => {
        const rect = document.querySelector("h1").getBoundingClientRect();
        return {
          left: rect.left,
          right: rect.right,
          top: rect.top,
          bottom: rect.bottom,
        };
      })(),
    }));
    assert.ok(
      stageBounds.scrollWidth <= stageBounds.width &&
        stageBounds.scrollHeight <= stageBounds.height,
    );
    assert.ok(
      stageBounds.title.left >= 0 &&
        stageBounds.title.right <= stageBounds.width &&
        stageBounds.title.top >= 0 &&
        stageBounds.title.bottom <= stageBounds.height,
    );
    pass("Existing holding output fits the 1366×768 projector", stageBounds);
  }
  for (const width of [390, 320]) {
    await desk.setViewportSize({ width, height: 844 });
    await until(
      async () =>
        desk.evaluate(
          () => document.documentElement.scrollWidth <= window.innerWidth,
        ),
      `Operator desk overflows horizontally at ${width}px`,
    );
    await desk.screenshot({
      path: join(output, `desk-${width}px.png`),
      fullPage: true,
    });
    pass(
      `Operator desk has no horizontal overflow at ${width}px`,
      await desk.evaluate(() => ({
        viewportWidth: innerWidth,
        scrollWidth: document.documentElement.scrollWidth,
      })),
    );
  }
  if (layoutOnly) {
    assert.deepEqual(
      await Promise.all([read("/runs"), read("/show"), read("/stage")]),
      [initialRuns, initialShow, initialStage],
    );
    assert.equal(evidence.mutations.length, 0);
    pass(
      "Layout follow-up preserved all runs, show configuration and global stage with zero writes",
    );
  }
  assert.deepEqual(evidence.violations, []);
  assert.deepEqual(evidence.browserErrors, []);
  pass(
    "No browser errors, external browser requests or live execution requests",
  );
  evidence.result = "passed";
} catch (error) {
  evidence.result = "failed";
  evidence.error = error.stack ?? String(error);
  if (desk)
    await desk
      .screenshot({ path: join(output, "failure-desk.png"), fullPage: true })
      .catch(() => {});
  if (stagePage)
    await stagePage
      .screenshot({ path: join(output, "failure-stage.png") })
      .catch(() => {});
  throw error;
} finally {
  await writeFile(
    join(output, "checks.json"),
    JSON.stringify(evidence, null, 2) + "\n",
  );
  await browser.close();
  console.log(`Evidence: ${output}`);
}
