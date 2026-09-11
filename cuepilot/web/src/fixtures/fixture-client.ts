import { ClientError, DEFAULT_NOTES, receiptMessage } from "../lib/model";
import type {
  CuePilotClient,
  CueReceipt,
  Run,
  Snapshot,
  Stage,
} from "../lib/model";
// Public fixture-storage identifier; this is not a credential.
const storageNamespace = "cuepilot.explicit-fixture.contract-v1";
type Store = Snapshot & { version: 1; requests: Record<string, CueReceipt> };
const now = () => new Date().toISOString();
const holding = (revision = 0, reason: string | null = null): Stage => ({
  revision,
  scene: "holding",
  speakerId: null,
  title: "We'll be right with you",
  subtitle: "CuePilot",
  assetId: null,
  reason,
  updatedAt: now(),
});
function initial(): Store {
  return {
    version: 1,
    requests: {},
    run: null,
    stage: holding(),
    show: {
      id: "fixture-demo",
      revision: 1,
      title: "CuePilot — From rehearsal to recall",
      speakers: [
        {
          id: "maya",
          name: "Maya Chen",
          title: "Opening speaker",
          ready: true,
          presentationAssetId: "slides-maya",
        },
        {
          id: "ravi",
          name: "Ravi Shah",
          title: "Product demo",
          ready: true,
          presentationAssetId: "slides-ravi",
        },
        {
          id: "alex",
          name: "Alex Rivera",
          title: "Closing speaker",
          ready: true,
          presentationAssetId: "slides-alex",
        },
      ],
      assets: [
        {
          id: "slides-maya",
          title: "Learn from rehearsal",
          kind: "slide",
          status: "ready",
        },
        {
          id: "slides-ravi",
          title: "Reuse the successful sequence",
          kind: "slide",
          status: "ready",
        },
        {
          id: "slides-alex",
          title: "Check before the next cue",
          kind: "slide",
          status: "ready",
        },
      ],
    },
  };
}
function load(): Store {
  const raw = localStorage.getItem(storageNamespace);
  if (!raw) return initial();
  try {
    const data = JSON.parse(raw) as Store;
    if (
      data.version !== 1 ||
      !data.show?.speakers?.length ||
      !data.stage ||
      !data.requests
    )
      throw new Error();
    return data;
  } catch {
    throw new ClientError(
      "Saved fixture data is unreadable. Clear the CuePilot fixture entry in browser storage to start again.",
      "FIXTURE_STORAGE",
    );
  }
}
function revision(s: Store, expected: number) {
  if (s.show.revision !== expected)
    throw new ClientError(
      "Show state changed. Create and approve a new plan.",
      "show_changed",
    );
}
function requireRun(s: Store, id: string): Run {
  if (!s.run || s.run.id !== id)
    throw new ClientError(
      "This fixture run is no longer current.",
      "run_not_found",
    );
  return s.run;
}
function ready(s: Store, speakerId: string) {
  const speaker = s.show.speakers.find((x) => x.id === speakerId);
  if (!speaker)
    throw new ClientError("Speaker does not exist.", "speaker_not_found");
  const asset = s.show.assets.find((x) => x.id === speaker.presentationAssetId);
  if (!speaker.ready)
    throw new ClientError(
      "The speaker is not ready. Stage stays on holding.",
      "speaker_unavailable",
    );
  if (!asset || asset.status !== "ready")
    throw new ClientError(
      "The presentation is unavailable. Stage stays on holding.",
      "presentation_unavailable",
    );
  return { speaker, asset };
}
function interruptIfNeeded(s: Store) {
  if (!s.stage.speakerId) return;
  try {
    ready(s, s.stage.speakerId);
  } catch (error) {
    const reason =
      error instanceof Error ? error.message : "Readiness changed.";
    s.stage = holding(s.stage.revision + 1, reason);
    if (s.run) {
      s.run.status = "blocked";
      s.run.reason = reason;
      s.run.updatedAt = now();
    }
  }
}
async function mutate(fn: (s: Store) => Promise<string> | string) {
  if (!navigator.locks)
    throw new ClientError(
      "Fixture rehearsal requires a secure loopback browser with Web Locks support.",
      "FIXTURE_LOCKS",
    );
  return navigator.locks.request(storageNamespace, async () => {
    const s = load();
    try {
      const message = await fn(s);
      localStorage.setItem(storageNamespace, JSON.stringify(s));
      return { message: `Fixture data · ${message}` };
    } catch (error) {
      localStorage.setItem(storageNamespace, JSON.stringify(s));
      throw error;
    }
  });
}
export const fixtureClient: CuePilotClient = {
  source: "fixture",
  async read() {
    return load();
  },
  setSpeakerReady(id, value, expectedRevision) {
    return mutate((s) => {
      revision(s, expectedRevision);
      const speaker = s.show.speakers.find((x) => x.id === id);
      if (!speaker)
        throw new ClientError("Speaker does not exist.", "speaker_not_found");
      if (speaker.ready !== value) {
        speaker.ready = value;
        s.show.revision++;
        interruptIfNeeded(s);
      }
      return `Speaker marked ${value ? "ready" : "unavailable"}.`;
    });
  },
  setAssetStatus(id, status, expectedRevision) {
    return mutate((s) => {
      revision(s, expectedRevision);
      const asset = s.show.assets.find((x) => x.id === id);
      if (!asset)
        throw new ClientError("Presentation does not exist.", "item_not_found");
      if (asset.status !== status) {
        asset.status = status;
        s.show.revision++;
        interruptIfNeeded(s);
      }
      return `Presentation marked ${status}.`;
    });
  },
  createRun(speakerId, executionMode, notes) {
    return mutate(async (s) => {
      if (executionMode === "live")
        throw new ClientError(
          "Fixture data cannot execute sponsor work. Select the API source to check live setup.",
          "LIVE_UNAVAILABLE",
        );
      const ruleText = notes ?? DEFAULT_NOTES;
      if (s.run?.status === "running")
        throw new ClientError(
          "Finish this fixture segment before creating another.",
          "stage_busy",
        );
      ready(s, speakerId);
      const id = crypto.randomUUID();
      const plan = {
        id: crypto.randomUUID(),
        hash: "",
        recipeId: "speaker-segment-v1",
        recipeVersion: 1,
        showRevision: s.show.revision,
        speakerId,
        cues: [
          { index: 0, scene: "intro" },
          { index: 1, scene: "presentation" },
          { index: 2, scene: "holding" },
        ],
        origin: "fixture",
      } satisfies NonNullable<Run["plan"]>;
      const digest = await crypto.subtle.digest(
        "SHA-256",
        new TextEncoder().encode(JSON.stringify({ plan, notes: ruleText, id })),
      );
      plan.hash = Array.from(new Uint8Array(digest), (b) =>
        b.toString(16).padStart(2, "0"),
      ).join("");
      s.run = {
        id,
        showId: s.show.id,
        speakerId,
        executionMode,
        status: "needs_approval",
        notes: ruleText,
        plan,
        nextStep: 0,
        receipts: [],
        traces: [
          {
            provider: "local",
            status: "fixture",
            operation: "prepare",
            evidence: { template: plan.recipeId },
            reason: "Local UI rehearsal; sponsor pipeline has not executed.",
          },
        ],
        reason: null,
        createdAt: now(),
        updatedAt: now(),
      };
      return "Practice plan created. Review the three cues before approval.";
    });
  },
  approve(id, planHash) {
    return mutate((s) => {
      const run = requireRun(s, id);
      if (run.status !== "needs_approval" || !run.plan)
        throw new ClientError(
          "This run has no pending plan to approve.",
          "approval_not_expected",
        );
      if (run.plan.hash !== planHash)
        throw new ClientError(
          "Approval must match the exact displayed plan.",
          "plan_changed",
        );
      ready(s, run.speakerId);
      revision(s, run.plan.showRevision);
      run.status = "approved";
      run.updatedAt = now();
      return "Plan approved.";
    });
  },
  advance(id, requestId, stepIndex) {
    return mutate((s) => {
      if (!Number.isInteger(stepIndex) || stepIndex < 0 || stepIndex > 2)
        throw new ClientError(
          "A valid frozen cue step is required.",
          "invalid_request",
        );
      const key = `${id}:${requestId}`;
      if (s.requests[key]) {
        if (s.requests[key].stepIndex !== stepIndex)
          throw new ClientError(
            "This request ID belongs to another cue.",
            "request_id_conflict",
          );
        return receiptMessage(s.requests[key]);
      }
      const run = requireRun(s, id);
      const accepted = run.receipts.find(
        (receipt) => receipt.stepIndex === stepIndex,
      );
      if (accepted) {
        s.requests[key] = accepted;
        return receiptMessage(accepted);
      }
      if (!["approved", "running"].includes(run.status) || !run.plan)
        throw new ClientError(
          "This cue requires an approved, active plan.",
          "approved_plan_required",
        );
      const cue = run.plan.cues.find((c) => c.index === stepIndex);
      if (!cue || stepIndex !== run.nextStep)
        throw new ClientError(
          "Execute only the next approved cue.",
          "cue_out_of_order",
        );
      try {
        ready(s, run.speakerId);
        revision(s, run.plan.showRevision);
      } catch (error) {
        run.status = "blocked";
        run.reason =
          error instanceof Error ? error.message : "The cue is blocked.";
        run.updatedAt = now();
        s.stage = holding(s.stage.revision + 1, run.reason);
        throw error;
      }
      const { speaker, asset } = ready(s, run.speakerId);
      s.stage = holding(s.stage.revision + 1);
      if (cue.scene !== "holding")
        Object.assign(s.stage, {
          scene: cue.scene,
          speakerId: speaker.id,
          title: cue.scene === "intro" ? speaker.name : asset.title,
          subtitle: cue.scene === "intro" ? speaker.title : speaker.name,
          assetId: cue.scene === "intro" ? null : asset.id,
        });
      const receipt: CueReceipt = {
        ok: true,
        id: crypto.randomUUID(),
        runId: run.id,
        stepIndex: cue.index,
        scene: cue.scene,
        stageRevision: s.stage.revision,
        committedAt: now(),
      };
      run.receipts.push(receipt);
      run.nextStep++;
      run.status =
        run.nextStep === run.plan.cues.length ? "completed" : "running";
      run.updatedAt = now();
      s.requests[key] = receipt;
      return receiptMessage(receipt);
    });
  },
  async execute() {
    throw new ClientError(
      "Fixture data cannot execute sponsor work.",
      "LIVE_UNAVAILABLE",
    );
  },
  cancel(id) {
    return mutate((s) => {
      const run = requireRun(s, id);
      if (["completed", "blocked", "failed"].includes(run.status))
        return `Run remains ${run.status}; no additional cues were accepted.`;
      run.status = "blocked";
      run.reason = "Cancelled by the operator. Prepare a new run to continue.";
      run.updatedAt = now();
      run.verifiedCompletion = false;
      s.stage = holding(s.stage.revision + 1, run.reason);
      run.traces.push({
        provider: "local",
        status: "fixture",
        operation: "cancel",
        evidence: { committedReceipts: run.receipts.length },
        reason: run.reason,
      });
      return "Run cancelled. Fixture stage is holding; committed receipts are retained.";
    });
  },
};
