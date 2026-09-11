import { test } from "node:test";
import assert from "node:assert/strict";
import { getClient } from "../src/lib/api-client";
import { ClientError } from "../src/lib/model";
import { inspectCueIntent, readIntent, sendCue } from "../src/lib/cue-intent";
import type { CuePilotClient } from "../src/lib/model";
const realFetch = globalThis.fetch;
test("projector reads only current stage and never depends on selected run or history", async () => {
  const calls: string[] = [];
  globalThis.fetch = async (input) => {
    const url = String(input);
    calls.push(url);
    if (url !== "/api/v1/stage")
      throw new Error("Unrelated run service unavailable");
    return new Response(
      JSON.stringify({
        scene: "presentation",
        title: "Current presentation",
        revision: 17,
      }),
    );
  };
  try {
    const stage = await getClient("api").readStage();
    assert.equal(stage.scene, "presentation");
    assert.equal(stage.revision, 17);
    assert.deepEqual(calls, ["/api/v1/stage"]);
  } finally {
    globalThis.fetch = realFetch;
  }
});
test("history selection reads an existing run without mutating the run or stage", async () => {
  const calls: { url: string; method: string }[] = [];
  const historyRun = {
    id: "history-run",
    speakerId: "maya",
    status: "completed",
    executionMode: "live",
    createdAt: "2026-09-11T12:00:00Z",
    nextStep: 3,
    notes: "Private notes omitted from history rows",
  };
  globalThis.fetch = async (input, options) => {
    const url = String(input);
    calls.push({ url, method: options?.method ?? "GET" });
    return new Response(
      JSON.stringify(
        url === "/api/v1/runs"
          ? [historyRun]
          : url.endsWith("/history-run")
            ? historyRun
            : url.endsWith("/stage")
              ? { scene: "holding" }
              : { revision: 1 },
      ),
    );
  };
  try {
    const client = getClient("api");
    const history = await client.listRuns();
    assert.equal(history[0].id, "history-run");
    assert.equal("notes" in history[0], false);
    await client.selectRun(history[0].id);
    const snapshot = await client.read();
    assert.equal(snapshot.run?.id, "history-run");
    assert.equal(snapshot.stage.scene, "holding");
    assert.ok(calls.every((call) => call.method === "GET"));
  } finally {
    globalThis.fetch = realFetch;
  }
});
test("a removed selected run recovers from current API history without fixture fallback", async () => {
  let removed = false;
  const replacement = {
    id: "replacement-run",
    speakerId: "ravi",
    executionMode: "practice",
    status: "completed",
  };
  globalThis.fetch = async (input) => {
    const url = String(input);
    if (url.endsWith("/removed-run"))
      return removed
        ? new Response(
            JSON.stringify({
              detail: { code: "run_not_found", message: "Run removed" },
            }),
            { status: 404 },
          )
        : new Response(JSON.stringify({ id: "removed-run" }));
    return new Response(
      JSON.stringify(
        url === "/api/v1/runs"
          ? [replacement]
          : url.endsWith("/stage")
            ? { scene: "holding" }
            : { revision: 1 },
      ),
    );
  };
  try {
    const client = getClient("api");
    await client.selectRun("removed-run");
    removed = true;
    assert.equal((await client.read()).run?.id, "replacement-run");
  } finally {
    globalThis.fetch = realFetch;
  }
});
function store(): Storage {
  const data = new Map<string, string>();
  return {
    getItem: (k) => data.get(k) ?? null,
    setItem: (k, v) => {
      data.set(k, v);
    },
    removeItem: (k) => {
      data.delete(k);
    },
    clear: () => data.clear(),
    key: (i) => [...data.keys()][i] ?? null,
    get length() {
      return data.size;
    },
  };
}
test("unreadable cue intent blocks writes without discarding the saved record", () => {
  const storage = store();
  storage.setItem("cuepilot.pending-cue.api", "{bad-json");
  const result = inspectCueIntent("api", () => storage);
  assert.equal(result.intent, null);
  assert.match(result.error, /Operator actions are paused/);
  assert.equal(storage.getItem("cuepilot.pending-cue.api"), "{bad-json");
  assert.deepEqual(
    inspectCueIntent("fixture", () => storage),
    {
      intent: null,
      error: "",
    },
  );
});
test("denied browser storage is reported without crashing or assuming an empty intent", () => {
  const result = inspectCueIntent("api", () => {
    throw new Error("Storage access denied");
  });
  assert.equal(result.intent, null);
  assert.match(result.error, /Saved cue state could not be read/);
});
test("frozen routes, fields, returned hash, and server-side authentication boundary", async () => {
  const calls: {
    url: string;
    method: string;
    body: unknown;
    headers: Headers;
  }[] = [];
  globalThis.fetch = async (input, options) => {
    const url = String(input);
    const body = options?.body ? JSON.parse(String(options.body)) : undefined;
    calls.push({
      url,
      method: options?.method ?? "GET",
      body,
      headers: new Headers(options?.headers),
    });
    let value: unknown = {};
    if (url.endsWith("/runs") && options?.method === "POST")
      value = {
        id: "run-1",
        executionMode: "practice",
        status: "needs_approval",
      };
    else if (url.includes("/assets/"))
      value = { revision: 7, assets: [{ id: "asset-1", status: "missing" }] };
    else if (url.includes("/speakers/"))
      value = {
        revision: 8,
        speakers: [{ id: "maya", name: "Maya", ready: false }],
      };
    else if (url.endsWith("/approve")) value = { status: "approved" };
    else if (url.endsWith("/advance"))
      value = { stepIndex: 0, scene: "intro", stageRevision: 1 };
    else if (url.endsWith("/execute")) value = { status: "approved" };
    return new Response(JSON.stringify(value), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    const client = getClient("api");
    await client.createRun("maya", "practice", "Exact notes");
    await client.setAssetStatus("asset-1", "missing", 6);
    await client.setSpeakerReady("maya", false, 7);
    await client.approve("run-1", "f".repeat(64));
    await client.advance("run-1", "intent-1", 0);
    await client.execute("run-1");
    await client.read();
    assert.deepEqual(
      calls.slice(0, 6).map(({ url, method, body }) => ({ url, method, body })),
      [
        {
          url: "/api/v1/runs",
          method: "POST",
          body: {
            speakerId: "maya",
            executionMode: "practice",
            notes: "Exact notes",
          },
        },
        {
          url: "/api/v1/assets/asset-1",
          method: "PATCH",
          body: { status: "missing", expectedRevision: 6 },
        },
        {
          url: "/api/v1/speakers/maya",
          method: "PATCH",
          body: { ready: false, expectedRevision: 7 },
        },
        {
          url: "/api/v1/runs/run-1/approve",
          method: "POST",
          body: { planHash: "f".repeat(64) },
        },
        {
          url: "/api/v1/runs/run-1/advance",
          method: "POST",
          body: { requestId: "intent-1", stepIndex: 0 },
        },
        { url: "/api/v1/runs/run-1/execute", method: "POST", body: {} },
      ],
    );
    assert.deepEqual(
      calls
        .slice(6)
        .map((c) => c.url)
        .sort(),
      ["/api/v1/runs/run-1", "/api/v1/show", "/api/v1/stage"],
    );
    assert.ok(calls.every((c) => !c.headers.has("Authorization")));
  } finally {
    globalThis.fetch = realFetch;
  }
});
test("API rejections preserve the backend reason and never return fixture data", async () => {
  globalThis.fetch = async () =>
    new Response(
      JSON.stringify({
        detail: { code: "show_changed", message: "Exact backend reason." },
      }),
      { status: 409 },
    );
  try {
    await assert.rejects(
      getClient("api").approve("run-1", "f".repeat(64)),
      (e) =>
        e instanceof ClientError &&
        e.code === "show_changed" &&
        e.message === "Exact backend reason." &&
        !e.uncertain,
    );
  } finally {
    globalThis.fetch = realFetch;
  }
});
test("unavailable GET fails visibly; lost mutation response has an uncertain outcome", async () => {
  globalThis.fetch = async () => {
    throw new TypeError("Network failed");
  };
  try {
    await assert.rejects(
      getClient("api").read(),
      (e) => e instanceof ClientError && !e.uncertain,
    );
    await assert.rejects(
      getClient("api").advance("run-1", "intent-1", 0),
      (e) => e instanceof ClientError && e.uncertain,
    );
  } finally {
    globalThis.fetch = realFetch;
  }
});
test("an uncertain cue freezes its run, request ID and displayed step across reload or another tab advancing", async () => {
  const storage = store();
  const calls: [string, string, number][] = [];
  let fail = true;
  const client = {
    source: "api",
    advance: async (runId: string, requestId: string, stepIndex: number) => {
      calls.push([runId, requestId, stepIndex]);
      if (fail) {
        fail = false;
        throw new ClientError("Lost response", "NETWORK_ERROR", true);
      }
      return { message: "Receipt returned" };
    },
  } as CuePilotClient;
  await assert.rejects(sendCue(client, "run-1", 0, storage));
  const saved = readIntent(storage, "api");
  assert.ok(saved);
  assert.equal(saved.stepIndex, 0);
  // Another tab may now display step 1 or even a different run. Reconciliation
  // must still target step 0 of the original run, never the new next cue.
  await sendCue(client, "another-current-run", 1, storage);
  assert.deepEqual(calls[0], calls[1]);
  assert.equal(calls[1][0], "run-1");
  assert.equal(calls[1][2], 0);
  assert.equal(readIntent(storage, "api"), null);
  await sendCue(client, "run-1", 1, storage);
  assert.notEqual(calls[2][1], calls[1][1]);
  assert.equal(calls[2][2], 1);
});
test("a definite rejection clears the intent; fixture and API intents stay separate", async () => {
  const storage = store();
  const client = {
    source: "api",
    advance: async () => {
      throw new ClientError("Not approved", "approved_plan_required");
    },
  } as unknown as CuePilotClient;
  await assert.rejects(sendCue(client, "run-1", 0, storage));
  assert.equal(readIntent(storage, "api"), null);
  assert.equal(readIntent(storage, "fixture"), null);
});

test("HTTP 502 preserves the exact saved cue until an identical retry returns its receipt", async () => {
  const storage = store();
  const calls: { requestId: string; stepIndex: number }[] = [];
  globalThis.fetch = async (_, options) => {
    calls.push(JSON.parse(String(options?.body)));
    if (calls.length === 1)
      return new Response(
        JSON.stringify({
          detail: {
            code: "upstream_failed",
            message: "Gateway could not confirm the response.",
          },
        }),
        { status: 502, headers: { "Content-Type": "application/json" } },
      );
    return new Response(
      JSON.stringify({
        ok: true,
        stepIndex: 1,
        scene: "presentation",
        stageRevision: 2,
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );
  };
  try {
    await assert.rejects(
      sendCue(getClient("api"), "run-502", 1, storage),
      (error) => error instanceof ClientError && error.uncertain,
    );
    const pending = readIntent(storage, "api");
    assert.equal(pending?.stepIndex, 1);
    assert.equal(pending?.requestId, calls[0].requestId);
    await sendCue(getClient("api"), "run-502", 2, storage);
    assert.deepEqual(calls[1], calls[0]);
    assert.equal(readIntent(storage, "api"), null);
  } finally {
    globalThis.fetch = realFetch;
  }
});

test("legacy pending cues remain unresolved and never acquire the current displayed step", async () => {
  const storage = store();
  const raw = JSON.stringify({ runId: "old-run", requestId: "old-request" });
  storage.setItem("cuepilot.pending-cue.api", raw);
  let sent = false;
  const client = {
    source: "api",
    advance: async () => {
      sent = true;
      return { message: "Should never send" };
    },
  } as unknown as CuePilotClient;
  assert.deepEqual(readIntent(storage, "api"), {
    runId: "old-run",
    requestId: "old-request",
    stepIndex: null,
  });
  await assert.rejects(
    sendCue(client, "new-run", 2, storage),
    (error) =>
      error instanceof ClientError &&
      error.code === "LEGACY_CUE_INTENT" &&
      error.uncertain,
  );
  assert.equal(sent, false);
  assert.equal(storage.getItem("cuepilot.pending-cue.api"), raw);
});

test("a new cue requires a supported integer step before any storage or network write", async () => {
  const storage = store();
  let sent = false;
  const client = {
    source: "api",
    advance: async () => {
      sent = true;
      return { message: "Should never send" };
    },
  } as unknown as CuePilotClient;
  for (const step of [-1, 3, 0.5, Number.NaN])
    await assert.rejects(
      sendCue(client, "run-1", step, storage),
      (error) =>
        error instanceof ClientError && error.code === "INVALID_CUE_STEP",
    );
  assert.equal(sent, false);
  assert.equal(storage.length, 0);
});

test("live notes may inherit backend rules and cancel uses its own bounded timeout", async () => {
  const calls: { url: string; method: string; body: unknown }[] = [];
  const timeouts: number[] = [];
  const realTimeout = AbortSignal.timeout;
  AbortSignal.timeout = (milliseconds) => {
    timeouts.push(milliseconds);
    return realTimeout(milliseconds);
  };
  globalThis.fetch = async (input, options) => {
    const url = String(input);
    const body = options?.body ? JSON.parse(String(options.body)) : undefined;
    calls.push({ url, method: options?.method ?? "GET", body });
    return new Response(
      JSON.stringify({
        id: "run-live",
        executionMode: "live",
        status: url.endsWith("/cancel") ? "blocked" : "queued",
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );
  };
  try {
    const client = getClient("api");
    await client.createRun("maya", "live");
    await client.createRun("maya", "live", "Explicit edited rules.");
    await client.cancel("run-live");
    assert.deepEqual(calls, [
      {
        url: "/api/v1/runs",
        method: "POST",
        body: { speakerId: "maya", executionMode: "live" },
      },
      {
        url: "/api/v1/runs",
        method: "POST",
        body: {
          speakerId: "maya",
          executionMode: "live",
          notes: "Explicit edited rules.",
        },
      },
      { url: "/api/v1/runs/run-live/cancel", method: "POST", body: {} },
    ]);
    assert.deepEqual(timeouts, [15000, 15000, 60000]);
  } finally {
    globalThis.fetch = realFetch;
    AbortSignal.timeout = realTimeout;
  }
});
