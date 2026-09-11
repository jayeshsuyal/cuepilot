import { liveCompletion } from "./model";
import type { Run, RunMode, Scene, Source, Stage } from "./model";

export const cueLabels: Record<Scene, string> = {
  intro: "Introduce speaker",
  presentation: "Bring up presentation",
  holding: "Return to holding",
};

const preferenceKey = (source: Source) => `cuepilot.run-mode.${source}`;

export function readRunMode(
  source: Source,
  storage?: Pick<Storage, "getItem">,
): RunMode {
  if (source === "fixture") return "practice";
  try {
    return (storage ?? localStorage).getItem(preferenceKey(source)) ===
      "practice"
      ? "practice"
      : "live";
  } catch {
    return "live";
  }
}

export function saveRunMode(
  source: Source,
  mode: RunMode,
  storage?: Pick<Storage, "setItem">,
): RunMode {
  const next = source === "fixture" ? "practice" : mode;
  try {
    (storage ?? localStorage).setItem(preferenceKey(source), next);
  } catch {
    // A denied preference write must not stop the current operator session.
  }
  return next;
}

export function unfinishedRun(
  run: Pick<Run, "status"> | null | undefined,
): boolean {
  return Boolean(
    run && !["completed", "blocked", "failed"].includes(run.status),
  );
}

export function cueGuidance(
  run: Run | null | undefined,
  stage: Stage | null | undefined,
  options: {
    retry?: boolean;
    stalePlan?: boolean;
    executeRequested?: boolean;
  } = {},
): { title: string; detail: string } {
  if (options.retry)
    return {
      title: "Reconcile the previous cue",
      detail:
        "Retry the same request to retrieve its result. Other actions stay paused until it is reconciled.",
    };
  if (!run)
    return {
      title: "Ready for a new segment",
      detail:
        "Choose a speaker and create a run, then review and approve its plan.",
    };
  if (run.status === "blocked" || run.status === "failed")
    return {
      title: `Run ${run.status}`,
      detail: `${run.reason ?? "The backend stopped this run."} Resolve the issue, then create and approve a new run.`,
    };
  if (run.status === "completed") {
    const verification =
      run.executionMode === "practice"
        ? "Practice used a fixture plan."
        : liveCompletion(run).fullyVerified
          ? "Live execution is verified by returned evidence."
          : "Physical cues completed; live verification is incomplete.";
    return {
      title: "Segment finished",
      detail: `${stage?.scene === "holding" ? "The stage is holding for the next segment." : "The current stage output is shown above."} ${verification} Choose a speaker and create a new run to continue.`,
    };
  }
  if (run.status === "queued")
    return {
      title: "Preparing the plan",
      detail:
        "Waiting for the backend to return a plan and its evidence. No cues execute before approval.",
    };
  if (options.stalePlan)
    return {
      title: "Plan needs to be replaced",
      detail:
        "The show configuration changed after planning. Cancel this run, then create and approve a fresh plan.",
    };
  if (run.status === "needs_approval")
    return {
      title: "Review and approve the plan",
      detail:
        "Check the speaker, cue sequence, and production notes below. Approval is tied to this exact plan.",
    };
  if (run.executionMode === "live")
    return run.status === "running" || options.executeRequested
      ? {
          title:
            run.status === "running"
              ? "Live sequence running"
              : "Execution requested",
          detail:
            "Watch the returned cue receipts and stage output. Live execution advances the approved sequence automatically.",
        }
      : {
          title: "Ready to execute live",
          detail:
            "The plan is approved. Execute live to start the full cue sequence; sponsor evidence will report the result.",
        };
  const next = run.plan?.cues.find((cue) => cue.index === run.nextStep);
  return {
    title: next ? `Next: ${cueLabels[next.scene]}` : "Waiting for run state",
    detail: next
      ? "Practice advances one cue at a time. Use the cue button when you are ready."
      : "Waiting for the backend to report the result of the accepted cue sequence.",
  };
}
