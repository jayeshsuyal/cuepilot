import type {
  ApiError,
  CueReceipt,
  Run,
  Show,
  Stage,
} from "../../../contracts/api";
import { ClientError, receiptMessage } from "./model";
import type { CuePilotClient, RunSummary, Source } from "./model";
import { fixtureClient } from "../fixtures/fixture-client";

const BASE = "/api/v1";
async function request<T>(
  path: string,
  method = "GET",
  body?: unknown,
  timeoutMs = 15000,
): Promise<T> {
  let response: Response;
  let data: unknown;
  try {
    response = await fetch(`${BASE}${path}`, {
      method,
      cache: "no-store",
      credentials: "omit",
      headers:
        body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(timeoutMs),
    });
    try {
      data = await response.json();
    } catch {
      if (response.ok) throw new Error("Invalid API response.");
      // An HTTP rejection stays a rejection even when a proxy returns text.
      data = null;
    }
  } catch {
    throw new ClientError(
      method === "GET"
        ? "API connection interrupted. Check the local backend at 127.0.0.1:8787. The last known state is retained."
        : "The response was not received. The action may have reached the API; state is being refreshed.",
      "NETWORK_ERROR",
      method !== "GET",
    );
  }
  if (!response.ok) {
    const detail = (data as Partial<ApiError> | null)?.detail;
    throw new ClientError(
      typeof detail?.message === "string"
        ? detail.message
        : `API request rejected (HTTP ${response.status}).`,
      typeof detail?.code === "string"
        ? detail.code
        : `HTTP_${response.status}`,
      method !== "GET" && response.status >= 500,
    );
  }
  return data as T;
}
const id = encodeURIComponent;
const runSelectionKey = "cuepilot.api.selected-run";
function savedRun(): string | null {
  try {
    return typeof sessionStorage === "undefined"
      ? null
      : sessionStorage.getItem(runSelectionKey);
  } catch {
    return null;
  }
}
let currentRunId: string | null = savedRun();
function selectCurrentRun(runId: string | null) {
  currentRunId = runId;
  try {
    if (typeof sessionStorage === "undefined") return;
    if (runId) sessionStorage.setItem(runSelectionKey, runId);
    else sessionStorage.removeItem(runSelectionKey);
  } catch {
    /* Storage restrictions do not prevent reading the selected run. */
  }
}
const summarizeRun = ({
  id,
  speakerId,
  executionMode,
  status,
  createdAt,
  nextStep,
}: Run): RunSummary => ({
  id,
  speakerId,
  executionMode,
  status,
  createdAt,
  nextStep,
});
async function readCurrentRun(): Promise<Run | Run[]> {
  if (!currentRunId) return request<Run[]>("/runs");
  const selected = currentRunId;
  try {
    return await request<Run>(`/runs/${id(selected)}`);
  } catch (error) {
    if (
      !(error instanceof ClientError) ||
      !["run_not_found", "HTTP_404"].includes(error.code)
    )
      throw error;
    if (currentRunId === selected) selectCurrentRun(null);
    else if (currentRunId) return request<Run>(`/runs/${id(currentRunId)}`);
    return request<Run[]>("/runs");
  }
}
const apiClient: CuePilotClient = {
  source: "api",
  readStage() {
    return request<Stage>("/stage");
  },
  async listRuns() {
    return (await request<Run[]>("/runs")).map(summarizeRun);
  },
  async selectRun(runId) {
    const run = await request<Run>(`/runs/${id(runId)}`);
    if (run.id !== runId)
      throw new ClientError(
        "The selected run could not be confirmed.",
        "RUN_ID_MISMATCH",
      );
    selectCurrentRun(run.id);
    return {
      message:
        "Viewing the selected run. The stage continues to show current output.",
    };
  },
  async read() {
    const [show, stage, current] = await Promise.all([
      request<Show>("/show"),
      request<Stage>("/stage"),
      readCurrentRun(),
    ]);
    const run = Array.isArray(current) ? (current[0] ?? null) : current;
    if (!currentRunId && run) selectCurrentRun(run.id);
    return { show, stage, run };
  },
  async setSpeakerReady(speakerId, ready, expectedRevision) {
    const show = await request<Show>(`/speakers/${id(speakerId)}`, "PATCH", {
      ready,
      expectedRevision,
    });
    const speaker = show.speakers.find((s) => s.id === speakerId);
    return {
      message: `API updated ${speaker?.name ?? "speaker"}: ${speaker?.ready ? "ready" : "unavailable"}. Show revision ${show.revision}.`,
    };
  },
  async setAssetStatus(assetId, status, expectedRevision) {
    const show = await request<Show>(`/assets/${id(assetId)}`, "PATCH", {
      status,
      expectedRevision,
    });
    const asset = show.assets.find((a) => a.id === assetId);
    return {
      message: `API updated presentation: ${asset?.status ?? "unknown"}. Show revision ${show.revision}.`,
    };
  },
  async createRun(speakerId, executionMode, notes) {
    const run = await request<Run>("/runs", "POST", {
      speakerId,
      executionMode,
      notes,
    });
    selectCurrentRun(run.id);
    return {
      message: `API created ${run.executionMode} run: ${run.status.replaceAll("_", " ")}.`,
    };
  },
  async approve(runId, planHash) {
    const run = await request<Run>(`/runs/${id(runId)}/approve`, "POST", {
      planHash,
    });
    return {
      message: `API returned plan status: ${run.status.replaceAll("_", " ")}.`,
    };
  },
  async advance(runId, requestId, stepIndex) {
    const receipt = await request<CueReceipt>(
      `/runs/${id(runId)}/advance`,
      "POST",
      { requestId, stepIndex },
    );
    return { message: receiptMessage(receipt) };
  },
  async execute(runId) {
    await request<Run>(`/runs/${id(runId)}/execute`, "POST", {});
    return {
      message:
        "Execution request acknowledged. Follow the run status, stage output, and returned evidence.",
    };
  },
  async cancel(runId) {
    const run = await request<Run>(
      `/runs/${id(runId)}/cancel`,
      "POST",
      {},
      60000,
    );
    return {
      message: `API returned cancellation status: ${run.status.replaceAll("_", " ")}. ${run.reason ?? "State refreshed from the backend."}`,
    };
  },
};
export const getClient = (source: Source): CuePilotClient =>
  source === "fixture" ? fixtureClient : apiClient;
