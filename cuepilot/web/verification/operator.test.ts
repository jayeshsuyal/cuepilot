import { test } from "node:test";
import assert from "node:assert/strict";
import { fixtureClient } from "../src/fixtures/fixture-client";
import { getClient } from "../src/lib/api-client";
import { ClientError, DEFAULT_NOTES, liveCompletion } from "../src/lib/model";
import type { Evidence, Run } from "../src/lib/model";
import {
  cueGuidance,
  readRunMode,
  saveRunMode,
  unfinishedRun,
} from "../src/lib/operator-state";

const trace = (
  status: Evidence["status"],
  operation = "execute",
): Evidence => ({
  provider: "rocketride",
  status,
  operation,
  evidence: {},
});
const run = (overrides: Partial<Run> = {}): Run => ({
  id: "test-run",
  showId: "test-show",
  speakerId: "maya",
  executionMode: "live",
  status: "completed",
  notes: DEFAULT_NOTES,
  plan: null,
  nextStep: 3,
  receipts: [],
  traces: [],
  reason: null,
  createdAt: "2026-09-11T00:00:00Z",
  updatedAt: "2026-09-11T00:00:00Z",
  ...overrides,
});

test("mode preference survives reload per source and fixture always stays Practice", () => {
  const values = new Map<string, string>();
  const storage = {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
  };
  assert.equal(readRunMode("api", storage), "live");
  assert.equal(readRunMode("fixture", storage), "practice");
  saveRunMode("api", "practice", storage);
  assert.equal(readRunMode("api", storage), "practice");
  saveRunMode("fixture", "live", storage);
  assert.equal(readRunMode("fixture", storage), "practice");
  assert.equal(readRunMode("api", storage), "practice");
  saveRunMode("api", "live", storage);
  assert.equal(readRunMode("api", storage), "live");
  values.set("cuepilot.run-mode.api", "corrupt-value");
  assert.equal(readRunMode("api", storage), "live");
});

test("unavailable preference storage does not interrupt the operator session", () => {
  const storage = {
    getItem: (): string | null => {
      throw new Error("Storage denied");
    },
    setItem: () => {
      throw new Error("Storage denied");
    },
  };
  assert.equal(readRunMode("api", storage), "live");
  assert.equal(saveRunMode("api", "practice", storage), "practice");
  assert.equal(saveRunMode("fixture", "live", storage), "practice");
});

test("unfinished runs remain available for completion or cancellation before replacement", () => {
  for (const status of [
    "queued",
    "needs_approval",
    "approved",
    "running",
  ] as const)
    assert.equal(unfinishedRun({ status }), true);
  for (const status of ["completed", "blocked", "failed"] as const)
    assert.equal(unfinishedRun({ status }), false);
  assert.equal(unfinishedRun(null), false);
});

test("completed Practice explains holding and the next segment without claiming sponsor verification", () => {
  const completed = cueGuidance(run({ executionMode: "practice" }), {
    scene: "holding",
    revision: 12,
    speakerId: null,
    title: "Holding",
    subtitle: "CuePilot",
    assetId: null,
    reason: null,
    updatedAt: "2026-09-11T00:00:00Z",
  });
  assert.equal(completed.title, "Segment finished");
  assert.match(completed.detail, /stage is holding/);
  assert.match(completed.detail, /create a new run/);
  assert.match(completed.detail, /Practice used a fixture plan/);
  assert.doesNotMatch(completed.detail, /execution is verified/);
  assert.match(
    cueGuidance(run(), null).detail,
    /live verification is incomplete/,
  );
});

test("operator guidance follows approval, failure, retry and live execution states", () => {
  assert.equal(
    cueGuidance(run({ status: "needs_approval" }), null).title,
    "Review and approve the plan",
  );
  assert.equal(
    cueGuidance(run({ status: "queued" }), null).title,
    "Preparing the plan",
  );
  assert.equal(
    cueGuidance(run({ status: "approved" }), null).title,
    "Ready to execute live",
  );
  assert.equal(
    cueGuidance(run({ status: "running" }), null).title,
    "Live sequence running",
  );
  assert.equal(
    cueGuidance(run({ status: "approved" }), null, { executeRequested: true })
      .title,
    "Execution requested",
  );
  assert.equal(
    cueGuidance(run({ status: "approved" }), null, { stalePlan: true }).title,
    "Plan needs to be replaced",
  );
  const blocked = cueGuidance(
    run({ status: "blocked", reason: "Presentation missing." }),
    null,
  );
  assert.equal(blocked.title, "Run blocked");
  assert.match(blocked.detail, /Presentation missing/);
  assert.equal(
    cueGuidance(run({ status: "blocked" }), null, { retry: true }).title,
    "Reconcile the previous cue",
  );
});

test("practice guidance names the exact next cue from the returned plan", () => {
  const active = run({
    executionMode: "practice",
    status: "approved",
    nextStep: 1,
    plan: {
      id: "plan",
      hash: "hash",
      recipeId: "recipe",
      recipeVersion: 1,
      showRevision: 9,
      speakerId: "maya",
      origin: "fixture",
      cues: [
        { index: 0, scene: "intro" },
        { index: 1, scene: "presentation" },
        { index: 2, scene: "holding" },
      ],
    },
  });
  assert.equal(cueGuidance(active, null).title, "Next: Bring up presentation");
  assert.equal(
    cueGuidance({ ...active, nextStep: 2, status: "running" }, null).title,
    "Next: Return to holding",
  );
});

test("physical completion alone never claims verified live execution", () => {
  assert.equal(liveCompletion(run()).physicalCompleted, true);
  assert.equal(liveCompletion(run()).fullyVerified, false);
  assert.equal(
    liveCompletion(run({ verifiedCompletion: true })).fullyVerified,
    false,
  );
  assert.equal(
    liveCompletion(run({ traces: [trace("verified")] })).fullyVerified,
    false,
  );
  assert.equal(
    liveCompletion(
      run({ verifiedCompletion: true, traces: [trace("verified", "prepare")] }),
    ).fullyVerified,
    false,
  );
  assert.equal(
    liveCompletion(
      run({ verifiedCompletion: true, traces: [trace("verified")] }),
    ).fullyVerified,
    true,
  );
});

test("latest RocketRide execute evidence and live mode govern the completion label", () => {
  const latest = liveCompletion(
    run({
      verifiedCompletion: true,
      traces: [trace("verified"), trace("blocked")],
    }),
  );
  assert.equal(latest.rocketrideStatus, "blocked");
  assert.equal(latest.fullyVerified, false);
  assert.equal(
    liveCompletion(
      run({
        executionMode: "practice",
        verifiedCompletion: true,
        traces: [trace("verified")],
      }),
    ).fullyVerified,
    false,
  );
  assert.equal(
    liveCompletion(
      run({
        status: "running",
        verifiedCompletion: true,
        traces: [trace("verified")],
      }),
    ).fullyVerified,
    false,
  );
});

test("non-JSON mutation rejections retain HTTP uncertainty semantics", async () => {
  const originalFetch = globalThis.fetch;
  try {
    for (const status of [409, 502, 200]) {
      globalThis.fetch = async () =>
        new Response("Plain-text response", { status });
      await assert.rejects(
        getClient("api").cancel("test-run"),
        (error) =>
          error instanceof ClientError && error.uncertain === (status !== 409),
      );
    }
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("fixture cancellation holds the stage, retains receipts, and preserves frozen-step retries", async () => {
  const savedStorage = Object.getOwnPropertyDescriptor(
    globalThis,
    "localStorage",
  );
  const savedNavigator = Object.getOwnPropertyDescriptor(
    globalThis,
    "navigator",
  );
  const values = new Map<string, string>();
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
    },
  });
  Object.defineProperty(globalThis, "navigator", {
    configurable: true,
    value: {
      locks: {
        request: async (_name: string, action: () => unknown) => action(),
      },
    },
  });
  try {
    await fixtureClient.createRun("maya", "practice");
    const prepared = (await fixtureClient.read()).run!;
    await fixtureClient.approve(prepared.id, prepared.plan!.hash);
    await assert.rejects(
      fixtureClient.advance(prepared.id, "wrong-first", 1),
      (error) =>
        error instanceof ClientError && error.code === "cue_out_of_order",
    );
    await fixtureClient.advance(prepared.id, "intro-request", 0);
    const intro = (await fixtureClient.read()).run!.receipts[0];
    await fixtureClient.advance(prepared.id, "same-step-new-request", 0);
    assert.equal((await fixtureClient.read()).run!.receipts.length, 1);
    await assert.rejects(
      fixtureClient.advance(prepared.id, "intro-request", 1),
      (error) =>
        error instanceof ClientError && error.code === "request_id_conflict",
    );
    const result = await fixtureClient.cancel(prepared.id);
    assert.match(result.message, /^Fixture data/);
    const cancelled = await fixtureClient.read();
    assert.equal(cancelled.stage.scene, "holding");
    assert.equal(cancelled.run!.status, "blocked");
    assert.deepEqual(cancelled.run!.receipts, [intro]);
    assert.equal(cancelled.run!.traces.at(-1)!.status, "fixture");
    assert.equal(cancelled.run!.verifiedCompletion, false);
    await fixtureClient.cancel(prepared.id);
    assert.equal(
      (await fixtureClient.read()).stage.revision,
      cancelled.stage.revision,
    );
    await fixtureClient.advance(prepared.id, "retrieve-accepted-intro", 0);
    assert.deepEqual((await fixtureClient.read()).run!.receipts, [intro]);
    await assert.rejects(
      fixtureClient.advance(prepared.id, "presentation-after-cancel", 1),
      (error) =>
        error instanceof ClientError && error.code === "approved_plan_required",
    );
  } finally {
    if (savedStorage)
      Object.defineProperty(globalThis, "localStorage", savedStorage);
    else Reflect.deleteProperty(globalThis, "localStorage");
    if (savedNavigator)
      Object.defineProperty(globalThis, "navigator", savedNavigator);
    else Reflect.deleteProperty(globalThis, "navigator");
  }
});
