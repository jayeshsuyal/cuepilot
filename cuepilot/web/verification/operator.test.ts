import { test } from "node:test";
import assert from "node:assert/strict";
import { fixtureClient } from "../src/fixtures/fixture-client";
import { getClient } from "../src/lib/api-client";
import { ClientError, DEFAULT_NOTES, liveCompletion } from "../src/lib/model";
import type { Evidence, Run } from "../src/lib/model";

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
