import type {
  ApiError,
  CueReceipt,
  Run,
  Show,
  Stage,
} from "../../../contracts/api";
import { ClientError, receiptMessage } from "./model";
import type { CuePilotClient, Source } from "./model";
import { fixtureClient } from "../fixtures/fixture-client";

const BASE = "/api/v1";
async function request<T>(
  path: string,
  method = "GET",
  body?: unknown,
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
      signal: AbortSignal.timeout(15000),
    });
    data = await response.json();
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
    );
  }
  return data as T;
}
const id = encodeURIComponent;
let currentRunId: string | null = null;
const apiClient: CuePilotClient = {
  source: "api",
  async read() {
    const [show, stage, current] = await Promise.all([
      request<Show>("/show"),
      request<Stage>("/stage"),
      currentRunId
        ? request<Run>(`/runs/${id(currentRunId)}`)
        : request<Run[]>("/runs"),
    ]);
    const run = Array.isArray(current) ? (current[0] ?? null) : current;
    if (!currentRunId && run) currentRunId = run.id;
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
    currentRunId = run.id;
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
  async advance(runId, requestId) {
    const receipt = await request<CueReceipt>(
      `/runs/${id(runId)}/advance`,
      "POST",
      { requestId },
    );
    return { message: receiptMessage(receipt) };
  },
  async execute(runId) {
    const run = await request<Run>(`/runs/${id(runId)}/execute`, "POST", {});
    return {
      message: `API accepted the execution request. Current status: ${run.status.replaceAll("_", " ")}. Waiting for returned evidence and receipts.`,
    };
  },
};
export const getClient = (source: Source): CuePilotClient =>
  source === "fixture" ? fixtureClient : apiClient;
