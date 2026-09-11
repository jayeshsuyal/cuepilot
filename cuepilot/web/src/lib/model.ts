import type {
  Asset,
  CueReceipt,
  Run,
  Show,
  Stage,
} from "../../../contracts/api";
export type {
  Asset,
  Cue,
  CueReceipt,
  Evidence,
  Plan,
  Run,
  Scene,
  Show,
  Speaker,
  Stage,
} from "../../../contracts/api";
export type Source = "api" | "fixture";
export type RunMode = Run["executionMode"];
export type Snapshot = { show: Show; stage: Stage; run: Run | null };
export type ActionResult = { message: string };
export interface CuePilotClient {
  source: Source;
  read(): Promise<Snapshot>;
  setSpeakerReady(
    id: string,
    ready: boolean,
    expectedRevision: number,
  ): Promise<ActionResult>;
  setAssetStatus(
    id: string,
    status: Asset["status"],
    expectedRevision: number,
  ): Promise<ActionResult>;
  createRun(
    speakerId: string,
    executionMode: RunMode,
    notes: string,
  ): Promise<ActionResult>;
  approve(runId: string, planHash: string): Promise<ActionResult>;
  advance(runId: string, requestId: string): Promise<ActionResult>;
  execute(runId: string): Promise<ActionResult>;
}
export class ClientError extends Error {
  constructor(
    message: string,
    public readonly code: string,
    public readonly uncertain = false,
  ) {
    super(message);
    this.name = "ClientError";
  }
}
export const receiptMessage = (receipt: CueReceipt) =>
  `Cue ${receipt.stepIndex + 1} accepted: ${receipt.scene}. Stage revision ${receipt.stageRevision}.`;
export const DEFAULT_NOTES =
  "For every speaker, show their introduction, then their presentation, then return to holding. If a speaker or presentation is unavailable, stay on holding.";
