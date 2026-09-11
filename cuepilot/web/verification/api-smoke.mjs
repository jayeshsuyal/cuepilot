import assert from "node:assert/strict";
import { readFileSync, readdirSync, writeFileSync } from "node:fs";
const base = "http://127.0.0.1:5173/api/v1";
const evidence = {
  timestamp: new Date().toISOString(),
  scope: "Unchanged backend with isolated local frontend verification data",
  checks: [],
};
async function call(
  path,
  method = "GET",
  body,
  extraHeaders = {},
  direct = false,
) {
  const res = await fetch(
    `${direct ? "http://127.0.0.1:8787/api/v1" : base}${path}`,
    {
      method,
      headers:
        body === undefined
          ? extraHeaders
          : { "Content-Type": "application/json", ...extraHeaders },
      body: body === undefined ? undefined : JSON.stringify(body),
    },
  );
  return { status: res.status, body: await res.json() };
}
const check = (name, detail) => {
  evidence.checks.push({ name, result: "passed", detail });
  console.log("PASS", name);
};
const show = (await call("/show")).body;
const unauth = await call(
  "/runs",
  "POST",
  { speakerId: "alex", executionMode: "practice" },
  {},
  true,
);
assert.equal(unauth.status, 401);
check("Direct mutation requires the operator token", unauth.body);
const crossOrigin = await call(
  "/runs",
  "POST",
  { speakerId: "alex", executionMode: "practice" },
  { Origin: "https://untrusted.example" },
);
assert.equal(crossOrigin.status, 403);
check("Vite rejects cross-origin writes", crossOrigin.body);
const stale = await call("/assets/slides-alex", "PATCH", {
  status: "missing",
  expectedRevision: show.revision - 1,
});
assert.equal(stale.status, 409);
assert.equal(stale.body.detail.code, "show_changed");
assert.deepEqual((await call("/show")).body, show);
check("Stale revision rejects without changing show state", stale.body);
const created = await call("/runs", "POST", {
  speakerId: "alex",
  executionMode: "practice",
  notes: "Frontend API verification: introduce speaker, presentation, holding.",
});
assert.equal(created.status, 201);
const run = created.body;
assert.equal(run.plan.origin, "fixture");
check(
  "Proxy creates an actual API practice plan without a browser credential",
  { id: run.id, status: run.status, planHash: run.plan.hash },
);
const beforeApproval = await call(`/runs/${run.id}/advance`, "POST", {
  requestId: crypto.randomUUID(),
});
assert.equal(beforeApproval.status, 409);
check("Unapproved cue rejected by backend", beforeApproval.body);
const wrongHash = await call(`/runs/${run.id}/approve`, "POST", {
  planHash: "0".repeat(64),
});
assert.equal(wrongHash.status, 409);
check("Wrong plan hash rejected", wrongHash.body);
const approved = await call(`/runs/${run.id}/approve`, "POST", {
  planHash: run.plan.hash,
});
assert.equal(approved.body.status, "approved");
check("Exact returned hash approved", { status: approved.body.status });
const requestId = crypto.randomUUID();
const first = await call(`/runs/${run.id}/advance`, "POST", { requestId });
assert.equal(first.status, 200);
const firstStage = (await call("/stage")).body;
const duplicate = await call(`/runs/${run.id}/advance`, "POST", { requestId });
assert.deepEqual(duplicate.body, first.body);
assert.deepEqual((await call("/stage")).body, firstStage);
assert.equal((await call(`/runs/${run.id}`)).body.receipts.length, 1);
check("Duplicate request returns the original receipt without advancing", {
  requestId,
  receipt: first.body,
});
const presentation = await call(`/runs/${run.id}/advance`, "POST", {
  requestId: crypto.randomUUID(),
});
assert.equal(presentation.body.scene, "presentation");
assert.equal((await call("/stage")).body.scene, "presentation");
const holding = await call(`/runs/${run.id}/advance`, "POST", {
  requestId: crypto.randomUUID(),
});
assert.equal(holding.body.scene, "holding");
const complete = (await call(`/runs/${run.id}`)).body;
assert.equal(complete.status, "completed");
assert.deepEqual(
  complete.receipts.map((r) => r.stepIndex),
  [0, 1, 2],
);
check("All three cues committed in backend order", {
  runId: run.id,
  receipts: complete.receipts,
});
const tokenLine = readFileSync(".env.local", "utf8")
  .split("\n")
  .find((x) => x.startsWith("CUEPILOT_OPERATOR_TOKEN="));
const token = tokenLine?.slice(tokenLine.indexOf("=") + 1);
assert.ok(token && token.length > 20);
for (const file of readdirSync("dist/assets")) {
  const body = readFileSync(`dist/assets/${file}`, "utf8");
  assert.equal(
    body.includes(token),
    false,
    `${file} contains the local operator token`,
  );
  assert.equal(
    body.includes("CUEPILOT_OPERATOR_TOKEN"),
    false,
    `${file} references the server secret`,
  );
}
check(
  "Built client contains neither the operator token nor its variable name",
  { assets: readdirSync("dist/assets") },
);
writeFileSync(
  "verification/api-checks.json",
  JSON.stringify(evidence, null, 2) + "\n",
);
