import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { setTimeout as delay } from "node:timers/promises";

// Run after browser-demo.mjs against a disposable API with sponsor keys disabled.
// This suite never targets the real 5173/8787 services or invokes live execution.
const base = new URL(process.env.BASE_URL ?? "http://127.0.0.1:5177");
assert.equal(base.origin, "http://127.0.0.1:5177");
const output = resolve(
  process.env.EVIDENCE_DIR ??
    join(
      dirname(fileURLToPath(import.meta.url)),
      "../../.runtime/browser-sweep",
      new Date().toISOString().replaceAll(":", "-"),
    ),
);
await mkdir(output, { recursive: true });
const { chromium } = await import(
  pathToFileURL(
    process.env.PLAYWRIGHT_MODULE ??
      join(
        homedir(),
        ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs",
      ),
  ).href
);
const evidence = {
  timestamp: new Date().toISOString(),
  scope:
    "Actual Chrome against an isolated practice API; request failures are explicitly injected",
  checks: [],
  errors: [],
  mutations: [],
  setupWrites: [],
  blockedRequests: [],
  expectedNetworkErrors: [],
};
const browser = await chromium.launch({ channel: "chrome", headless: true });
let desk;
let stagePage;
let context;
let fault = {};
let droppedRequest;

async function api(path, method = "GET", body) {
  if (method !== "GET") evidence.setupWrites.push({ path, method });
  const response = await fetch(new URL(`/api/v1${path}`, base), {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(10000),
  });
  assert.ok(response.ok, `${method} ${path}: HTTP ${response.status}`);
  return response.json();
}
const snapshot = () => Promise.all([api("/runs"), api("/show"), api("/stage")]);
async function until(check, message, timeout = 8000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (await check()) return;
    await delay(80);
  }
  assert.fail(message);
}
async function clean() {
  for (const run of await api("/runs")) {
    assert.equal(
      run.executionMode,
      "practice",
      "The sweep must use a disposable practice-only API",
    );
    if (!["completed", "blocked", "failed"].includes(run.status))
      await api(`/runs/${run.id}/cancel`, "POST", {});
  }
  for (const asset of (await api("/show")).assets)
    if (asset.status !== "ready") {
      const show = await api("/show");
      await api(`/assets/${asset.id}`, "PATCH", {
        status: "ready",
        expectedRevision: show.revision,
      });
    }
  for (const speaker of (await api("/show")).speakers)
    if (!speaker.ready) {
      const show = await api("/show");
      await api(`/speakers/${speaker.id}`, "PATCH", {
        ready: true,
        expectedRevision: show.revision,
      });
    }
}
async function session(init) {
  if (context) await context.close();
  fault = {};
  context = await browser.newContext({
    viewport: { width: 1366, height: 900 },
    serviceWorkers: "block",
  });
  if (init) await context.addInitScript(init);
  context.on("page", (page) => {
    page.on("pageerror", (error) => evidence.errors.push(error.message));
    page.on("console", (message) => {
      if (message.type() !== "error") return;
      if (/Failed to load resource|net::ERR_FAILED/.test(message.text()))
        evidence.expectedNetworkErrors.push(message.text());
      else evidence.errors.push(message.text());
    });
  });
  await context.route("**/*", async (route) => {
    const request = route.request(),
      url = new URL(request.url());
    if (!["http:", "https:"].includes(url.protocol)) return route.continue();
    if (url.origin !== base.origin) {
      evidence.blockedRequests.push(url.origin + url.pathname);
      return route.abort();
    }
    const read = ["GET", "HEAD", "OPTIONS"].includes(request.method());
    if (!read) {
      const body = request.postDataJSON();
      const allowed =
        (request.method() === "POST" &&
          ((url.pathname === "/api/v1/runs" &&
            body?.executionMode === "practice") ||
            /^\/api\/v1\/runs\/[^/]+\/(approve|advance|cancel)$/.test(
              url.pathname,
            ))) ||
        (request.method() === "PATCH" &&
          /^\/api\/v1\/(assets|speakers)\/[^/]+$/.test(url.pathname));
      evidence.mutations.push({
        path: url.pathname,
        method: request.method(),
        body,
      });
      if (!allowed) {
        evidence.blockedRequests.push(request.method() + " " + url.pathname);
        return route.abort();
      }
      if (fault.dropAdvance && url.pathname.endsWith("/advance")) {
        fault.dropAdvance = false;
        const response = await route.fetch();
        assert.equal(response.status(), 200);
        droppedRequest = {
          path: url.pathname,
          body,
          receipt: await response.json(),
        };
        return route.abort("failed");
      }
    }
    if (
      read &&
      url.pathname.startsWith("/api/v1") &&
      (fault.offline || (fault.historyOnly && url.pathname === "/api/v1/runs"))
    )
      return route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({
          detail: {
            code: "injected_unavailable",
            message: "Synthetic verification outage",
          },
        }),
      });
    return route.continue();
  });
  desk = await context.newPage();
  return desk;
}
async function open() {
  await desk.goto(base.href);
  await desk.getByText("API connected", { exact: true }).waitFor();
  await desk.getByRole("radio", { name: "Practice", exact: true }).check();
}
async function mutation(button, path, method = "POST", status = 200) {
  const response = desk.waitForResponse(
    (r) =>
      new URL(r.url()).pathname === `/api/v1${path}` &&
      r.request().method() === method,
  );
  await button.click();
  const result = await response;
  assert.equal(result.status(), status);
  return result.json();
}
const createButton = () =>
  desk.getByRole("button", { name: /^Create (practice run|recovery plan)$/ });
async function create() {
  return mutation(createButton(), "/runs", "POST", 201);
}
async function approve(run) {
  return mutation(
    desk.getByRole("button", { name: "Approve plan", exact: true }),
    `/runs/${run.id}/approve`,
  );
}
async function cue(run, name) {
  return mutation(
    desk.getByRole("button", { name, exact: true }),
    `/runs/${run.id}/advance`,
  );
}
async function title(page, state) {
  await page
    .getByRole("region", { name: "Program output", exact: true })
    .getByRole("heading", { name: state.title, exact: true })
    .waitFor();
}
async function test(name, action) {
  console.log(`CHECK ${name}`);
  const startErrors = evidence.errors.length;
  try {
    const detail = await action();
    assert.deepEqual(
      evidence.errors.slice(startErrors),
      [],
      "Unexpected browser errors",
    );
    evidence.checks.push({ name, result: "passed", detail });
    console.log(`PASS ${name}`);
  } catch (error) {
    evidence.checks.push({ name, result: "failed", error: error.stack });
    console.error(`FAIL ${name}: ${error.message}`);
    await desk
      ?.screenshot({
        path: join(output, `${evidence.checks.length}-failure.png`),
        fullPage: true,
      })
      .catch(() => {});
  }
  fault = {};
  await clean();
}

try {
  await clean();
  await test("Initial API outage, visible disconnected state and automatic recovery", async () => {
    await session();
    fault.offline = true;
    const before = await snapshot();
    await desk.goto(base.href);
    await desk.getByText("API unavailable", { exact: true }).waitFor();
    assert.equal(await desk.getByLabel("Data source").inputValue(), "api");
    assert.equal(
      await desk
        .getByRole("button", { name: "Create live run", exact: true })
        .isDisabled(),
      true,
    );
    stagePage = await context.newPage();
    await stagePage.goto(new URL("/stage", base).href);
    await stagePage.getByText("SIGNAL UNAVAILABLE", { exact: true }).waitFor();
    fault.offline = false;
    await desk.getByText("API connected", { exact: true }).waitFor();
    await title(stagePage, before[2]);
    assert.deepEqual(await snapshot(), before);
    return { injected: "HTTP503 on reads", backendWrites: 0 };
  });
  await test("Connection lost preserves last stage and disables actions; recovery sends no writes", async () => {
    await session();
    await open();
    stagePage = await context.newPage();
    await stagePage.goto(new URL("/stage", base).href);
    const before = await snapshot();
    await title(stagePage, before[2]);
    fault.offline = true;
    await desk.getByText("API unavailable", { exact: true }).waitFor();
    await stagePage.getByText("Connection lost", { exact: true }).waitFor();
    await title(desk, before[2]);
    await title(stagePage, before[2]);
    assert.equal(await createButton().isDisabled(), true);
    fault.offline = false;
    await desk.getByText("API connected", { exact: true }).waitFor();
    await stagePage
      .getByText("Connection lost", { exact: true })
      .waitFor({ state: "hidden" });
    assert.deepEqual(await snapshot(), before);
  });
  await test("Rejected practice creation with missing presentation leaves no phantom run", async () => {
    await session();
    await open();
    await mutation(
      desk.getByRole("switch", { name: "Presentation ready" }),
      "/assets/slides-maya",
      "PATCH",
    );
    const before = await snapshot();
    await mutation(createButton(), "/runs", "POST", 409);
    await desk
      .getByText("Action could not complete", { exact: true })
      .waitFor();
    assert.deepEqual(await snapshot(), before);
    return { runCount: before[0].length, rejectedStatus: 409 };
  });
  await test("Approval boundary and cancellation before the first cue preserve zero receipts", async () => {
    await session();
    await open();
    const run = await create();
    assert.equal(
      await desk
        .getByRole("button", { name: "Introduce speaker", exact: true })
        .count(),
      0,
    );
    await approve(run);
    const final = await mutation(
      desk.getByRole("button", { name: "Cancel run", exact: true }),
      `/runs/${run.id}/cancel`,
    );
    assert.equal(final.status, "blocked");
    assert.equal(final.receipts.length, 0);
    assert.equal((await api("/stage")).scene, "holding");
    assert.equal(
      await desk
        .getByRole("button", { name: "Introduce speaker", exact: true })
        .count(),
      0,
    );
  });
  await test("Missing presentation after intro holds both outputs; restoring cannot resume old plan", async () => {
    await session();
    await open();
    const run = await create();
    await approve(run);
    await cue(run, "Introduce speaker");
    stagePage = await context.newPage();
    await stagePage.goto(new URL("/stage", base).href);
    await title(stagePage, await api("/stage"));
    await mutation(
      desk.getByRole("switch", { name: "Presentation ready" }),
      "/assets/slides-maya",
      "PATCH",
    );
    const interrupted = await api(`/runs/${run.id}`),
      state = await api("/stage");
    assert.equal(interrupted.status, "blocked");
    assert.equal(interrupted.receipts.length, 1);
    assert.equal(state.scene, "holding");
    await Promise.all([title(desk, state), title(stagePage, state)]);
    await mutation(
      desk.getByRole("switch", { name: "Presentation ready" }),
      "/assets/slides-maya",
      "PATCH",
    );
    assert.equal((await api(`/runs/${run.id}`)).status, "blocked");
    assert.equal((await api(`/runs/${run.id}`)).receipts.length, 1);
    assert.equal(
      await desk
        .getByRole("button", { name: "Bring up presentation", exact: true })
        .count(),
      0,
    );
    assert.equal((await api("/stage")).scene, "holding");
    return { committedReceipts: 1, restoredRunStatus: "blocked" };
  });
  await test("Changed configuration makes approval stale and leaves cancellation available", async () => {
    await session();
    await open();
    const run = await create();
    await mutation(
      desk.getByRole("switch", { name: "Presentation ready" }),
      "/assets/slides-maya",
      "PATCH",
    );
    await desk
      .getByText("Show changed after planning", { exact: true })
      .waitFor();
    assert.equal(
      await desk
        .getByRole("button", { name: "Approve plan", exact: true })
        .isDisabled(),
      true,
    );
    await mutation(
      desk.getByRole("button", { name: "Cancel run", exact: true }),
      `/runs/${run.id}/cancel`,
    );
    assert.equal((await api(`/runs/${run.id}`)).receipts.length, 0);
  });
  await test("A rapid double click commits only the intended first cue", async () => {
    await session();
    await open();
    const run = await create();
    await approve(run);
    const button = desk.getByRole("button", {
      name: "Introduce speaker",
      exact: true,
    });
    await button.dblclick({ delay: 30 });
    await delay(700);
    const final = await api(`/runs/${run.id}`);
    assert.equal(
      final.receipts.length,
      1,
      "One double-click gesture advanced multiple cues",
    );
    return { receipts: final.receipts.length, interClickDelayMs: 30 };
  });
  await test("Lost success response survives reload and retries the same request without another cue", async () => {
    await session();
    await open();
    const run = await create();
    await approve(run);
    fault.dropAdvance = true;
    droppedRequest = null;
    await desk
      .getByRole("button", { name: "Introduce speaker", exact: true })
      .click();
    await desk
      .getByRole("button", { name: "Retry same cue", exact: true })
      .waitFor();
    assert.ok(droppedRequest);
    const committed = await api(`/runs/${run.id}`),
      state = await api("/stage");
    assert.equal(committed.receipts.length, 1);
    assert.equal(await desk.getByLabel("Data source").isDisabled(), true);
    await desk.reload();
    await desk.getByText("API connected", { exact: true }).waitFor();
    const retried = await cue(run, "Retry same cue");
    assert.deepEqual(retried, droppedRequest.receipt);
    const retries = evidence.mutations.filter(
      (x) =>
        x.path === droppedRequest.path &&
        x.body.requestId === droppedRequest.body.requestId,
    );
    assert.equal(retries.length, 2);
    assert.deepEqual(retries[0].body, retries[1].body);
    assert.deepEqual(await api("/stage"), state);
    assert.equal((await api(`/runs/${run.id}`)).receipts.length, 1);
    await desk
      .getByRole("button", { name: "Bring up presentation", exact: true })
      .waitFor();
    return {
      sameRequestId: true,
      receipts: 1,
      injected: "Successful API response deliberately dropped",
    };
  });
  await test("Fixture/API switching keeps simulated stage and backend state isolated", async () => {
    await session();
    await open();
    const before = await snapshot();
    const beforeWrites = evidence.mutations.length;
    await desk.getByLabel("Data source").selectOption("fixture");
    assert.equal(
      await desk.getByRole("radio", { name: "Live", exact: true }).isDisabled(),
      true,
    );
    await createButton().click();
    await desk
      .getByRole("button", { name: "Approve plan", exact: true })
      .click();
    await desk
      .getByRole("button", { name: "Introduce speaker", exact: true })
      .click();
    await title(desk, { title: "Maya Chen" });
    stagePage = await context.newPage();
    await stagePage.goto(new URL("/stage?source=fixture", base).href);
    await title(stagePage, { title: "Maya Chen" });
    await desk.getByLabel("Data source").selectOption("api");
    await desk.getByText("API connected", { exact: true }).waitFor();
    await title(desk, before[2]);
    await desk.getByLabel("Data source").selectOption("fixture");
    await title(desk, { title: "Maya Chen" });
    assert.deepEqual(await snapshot(), before);
    assert.equal(evidence.mutations.length, beforeWrites);
    return {
      backendWrites: 0,
      fixtureScene: "intro",
      backendScene: before[2].scene,
    };
  });
  for (const mode of ["corrupt-pending", "denied-session-storage"])
    await test(`${mode}: readable desk/stage, visible warning and zero writes`, async () => {
      await session(
        mode === "corrupt-pending"
          ? () => {
              if (location.origin === "http://127.0.0.1:5177")
                sessionStorage.setItem(
                  "cuepilot.pending-cue.api",
                  "{invalid-json",
                );
            }
          : () => {
              if (location.origin === "http://127.0.0.1:5177")
                Object.defineProperty(window, "sessionStorage", {
                  get() {
                    throw new DOMException(
                      "Storage access denied",
                      "SecurityError",
                    );
                  },
                });
            },
      );
      const before = await snapshot(),
        writes = evidence.mutations.length;
      await desk.goto(base.href);
      await desk.getByText("API connected", { exact: true }).waitFor();
      await desk.getByRole("heading", { name: /Operator desk/ }).waitFor();
      await desk.getByText("Saved cue needs review", { exact: true }).waitFor();
      assert.equal(
        await desk.getByRole("button", { name: /^Create/ }).isDisabled(),
        true,
      );
      const complete = before[0].find((run) => run.status === "completed");
      assert.ok(complete);
      await desk.getByLabel("Run history").selectOption(complete.id);
      stagePage = await context.newPage();
      await stagePage.goto(new URL("/stage", base).href);
      await title(stagePage, before[2]);
      if (mode === "corrupt-pending")
        assert.equal(
          await desk.evaluate(() =>
            sessionStorage.getItem("cuepilot.pending-cue.api"),
          ),
          "{invalid-json",
        );
      assert.deepEqual(await snapshot(), before);
      assert.equal(evidence.mutations.length, writes);
      await desk.screenshot({
        path: join(output, `${mode}-fixed.png`),
        fullPage: true,
      });
    });
  await test("Keyboard navigation and narrow layouts retain usable controls without overflow", async () => {
    await session();
    await desk.goto(base.href);
    await desk.getByText("API connected", { exact: true }).waitFor();
    await desk.keyboard.press("Tab");
    assert.equal(
      await desk.evaluate(() => document.activeElement?.textContent?.trim()),
      "Skip to operator controls",
    );
    await desk.keyboard.press("Enter");
    assert.equal(new URL(desk.url()).hash, "#desk-controls");
    await desk.getByRole("radio", { name: "Practice", exact: true }).focus();
    await desk.keyboard.press("Space");
    assert.equal(
      await desk
        .getByRole("radio", { name: "Practice", exact: true })
        .isChecked(),
      true,
    );
    for (const width of [390, 320]) {
      await desk.setViewportSize({ width, height: 844 });
      assert.equal(
        await desk.evaluate(() => document.documentElement.scrollWidth),
        width,
      );
      await desk.screenshot({
        path: join(output, `keyboard-${width}px.png`),
        fullPage: true,
      });
    }
  });
  assert.deepEqual(evidence.blockedRequests, []);
  evidence.result = evidence.checks.some((check) => check.result === "failed")
    ? "failed"
    : "passed";
  if (evidence.result === "failed") process.exitCode = 1;
} finally {
  await writeFile(
    join(output, "checks.json"),
    JSON.stringify(evidence, null, 2) + "\n",
  );
  await browser.close();
  console.log(`Evidence: ${output}`);
}
