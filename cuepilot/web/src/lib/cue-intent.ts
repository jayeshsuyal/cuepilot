import { ClientError } from "./model";
import type { ActionResult, CuePilotClient, Source } from "./model";
type FrozenCueIntent = {
  runId: string;
  requestId: string;
  stepIndex: number;
};
export type CueIntent =
  FrozenCueIntent | { runId: string; requestId: string; stepIndex: null };
export function inspectCueIntent(
  source: Source,
  storage: () => Storage = () => sessionStorage,
): { intent: CueIntent | null; error: string } {
  try {
    return { intent: readIntent(storage(), source), error: "" };
  } catch {
    // Keep unreadable intent untouched. Losing it could duplicate a prior cue.
    return {
      intent: null,
      error:
        "Saved cue state could not be read. Operator actions are paused. Review the last run's receipts and restore browser storage before reloading; do not discard an unresolved cue.",
    };
  }
}
const key = (source: Source) => `cuepilot.pending-cue.${source}`;
const validStep = (value: unknown): value is number =>
  typeof value === "number" &&
  Number.isInteger(value) &&
  value >= 0 &&
  value <= 2;
export function readIntent(storage: Storage, source: Source): CueIntent | null {
  const raw = storage.getItem(key(source));
  if (!raw) return null;
  try {
    const value = JSON.parse(raw);
    if (
      !value ||
      typeof value !== "object" ||
      typeof value.runId !== "string" ||
      !value.runId ||
      typeof value.requestId !== "string" ||
      !value.requestId
    )
      throw new Error();
    // Legacy storage did not bind a cue. Keep it visible without guessing which
    // currently displayed step the operator originally intended to send.
    if (!("stepIndex" in value) || value.stepIndex === null)
      return {
        runId: value.runId,
        requestId: value.requestId,
        stepIndex: null,
      };
    if (!validStep(value.stepIndex)) throw new Error();
    return {
      runId: value.runId,
      requestId: value.requestId,
      stepIndex: value.stepIndex,
    };
  } catch {
    throw new Error(
      "Pending cue data is unreadable. Reconcile the last cue before continuing.",
    );
  }
}
export async function sendCue(
  client: CuePilotClient,
  runId: string,
  stepIndex: number,
  storage: Storage,
): Promise<ActionResult> {
  const saved = readIntent(storage, client.source);
  if (saved?.stepIndex === null)
    throw new ClientError(
      "This saved cue predates step-bound requests. No cue was sent. Inspect the saved run's receipts before clearing its pending cue data; the intended step cannot be inferred safely.",
      "LEGACY_CUE_INTENT",
      true,
    );
  if (!saved && !validStep(stepIndex))
    throw new ClientError(
      "The displayed cue step is invalid.",
      "INVALID_CUE_STEP",
    );
  const intent: FrozenCueIntent = saved ?? {
    runId,
    requestId: crypto.randomUUID(),
    stepIndex,
  };
  // Save before sending so an interrupted tab can retry the exact intent.
  storage.setItem(key(client.source), JSON.stringify(intent));
  try {
    const result = await client.advance(
      intent.runId,
      intent.requestId,
      intent.stepIndex,
    );
    storage.removeItem(key(client.source));
    return result;
  } catch (error) {
    if (!(error instanceof ClientError && error.uncertain))
      storage.removeItem(key(client.source));
    throw error;
  }
}
