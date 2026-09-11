import { ClientError } from "./model";
import type { ActionResult, CuePilotClient, Source } from "./model";
export type CueIntent = { runId: string; requestId: string };
const key = (source: Source) => `cuepilot.pending-cue.${source}`;
export function readIntent(storage: Storage, source: Source): CueIntent | null {
  const raw = storage.getItem(key(source));
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as CueIntent;
    if (typeof value.runId !== "string" || typeof value.requestId !== "string")
      throw new Error();
    return value;
  } catch {
    throw new Error(
      "Pending cue data is unreadable. Reconcile the last cue before continuing.",
    );
  }
}
export async function sendCue(
  client: CuePilotClient,
  runId: string,
  storage: Storage,
): Promise<ActionResult> {
  const intent = readIntent(storage, client.source) ?? {
    runId,
    requestId: crypto.randomUUID(),
  };
  // Save before sending so an interrupted tab can retry the exact intent.
  storage.setItem(key(client.source), JSON.stringify(intent));
  try {
    const result = await client.advance(intent.runId, intent.requestId);
    storage.removeItem(key(client.source));
    return result;
  } catch (error) {
    if (!(error instanceof ClientError && error.uncertain))
      storage.removeItem(key(client.source));
    throw error;
  }
}
