import { test } from "node:test";
import assert from "node:assert/strict";
import { getClient } from "../src/lib/api-client";
import { ClientError } from "../src/lib/model";
import { readIntent, sendCue } from "../src/lib/cue-intent";
import type { CuePilotClient } from "../src/lib/model";
const realFetch = globalThis.fetch;
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
    await client.advance("run-1", "intent-1");
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
          body: { requestId: "intent-1" },
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
      getClient("api").advance("run-1", "intent-1"),
      (e) => e instanceof ClientError && e.uncertain,
    );
  } finally {
    globalThis.fetch = realFetch;
  }
});
test("an uncertain cue keeps its request ID across reload and a changed current run", async () => {
  const storage = store();
  const calls: [string, string][] = [];
  let fail = true;
  const client = {
    source: "api",
    advance: async (runId: string, requestId: string) => {
      calls.push([runId, requestId]);
      if (fail) {
        fail = false;
        throw new ClientError("Lost response", "NETWORK_ERROR", true);
      }
      return { message: "Receipt returned" };
    },
  } as CuePilotClient;
  await assert.rejects(sendCue(client, "run-1", storage));
  const saved = readIntent(storage, "api");
  assert.ok(saved);
  await sendCue(client, "another-current-run", storage);
  assert.deepEqual(calls[0], calls[1]);
  assert.equal(calls[1][0], "run-1");
  assert.equal(readIntent(storage, "api"), null);
  await sendCue(client, "run-1", storage);
  assert.notEqual(calls[2][1], calls[1][1]);
});
test("a definite rejection clears the intent; fixture and API intents stay separate", async () => {
  const storage = store();
  const client = {
    source: "api",
    advance: async () => {
      throw new ClientError("Not approved", "approved_plan_required");
    },
  } as unknown as CuePilotClient;
  await assert.rejects(sendCue(client, "run-1", storage));
  assert.equal(readIntent(storage, "api"), null);
  assert.equal(readIntent(storage, "fixture"), null);
});
