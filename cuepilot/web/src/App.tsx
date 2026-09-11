import { useEffect, useState } from "react";
import {
  ArrowRight,
  ArrowUpRight,
  Check,
  ChevronRight,
  CircleHelp,
  Expand,
  FileText,
  Flag,
  Layers3,
  LoaderCircle,
  Monitor,
  Radio,
  RefreshCw,
  ShieldCheck,
  Square,
  TriangleAlert,
  Wifi,
  WifiOff,
} from "lucide-react";
import { Program } from "./components/Program";
import { ClientError, DEFAULT_NOTES, liveCompletion } from "./lib/model";
import type { Evidence, RunMode, Scene, Source } from "./lib/model";
import { useDesk } from "./lib/use-desk";
import {
  cueGuidance,
  cueLabels,
  preparationVerified,
  readRunMode,
  saveRunMode,
  unfinishedRun,
} from "./lib/operator-state";
import { useStage } from "./lib/use-stage";

const cueDetails: Record<Scene, string> = {
  intro: "Name and introduction on program",
  presentation: "Presentation readiness checked at cue time",
  holding: "Clear the stage for the next speaker",
};
const initialSource = (): Source =>
  new URLSearchParams(location.search).get("source") === "fixture"
    ? "fixture"
    : "api";
const time = (date: string) =>
  new Date(date).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
function EvidenceRow({ entry }: { entry: Evidence }) {
  const icon =
    entry.status === "verified" ? (
      <Check size={15} />
    ) : entry.status === "blocked" || entry.status === "failed" ? (
      <TriangleAlert size={15} />
    ) : (
      <Layers3 size={15} />
    );
  return (
    <div className={`evidence-row evidence-${entry.status}`}>
      <span className="trace-icon">{icon}</span>
      <div>
        <div className="evidence-title">
          <strong>
            {entry.provider} <span>· {entry.operation}</span>
          </strong>
          <span className="tiny-status">{entry.status}</span>
        </div>
        {entry.reason && <p>{entry.reason}</p>}
        <details>
          <summary>Inspect returned evidence</summary>
          <pre>
            {JSON.stringify(entry.evidence, null, 2) ??
              "No evidence payload returned."}
          </pre>
        </details>
      </div>
    </div>
  );
}
function Desk({
  source,
  onSource,
}: {
  source: Source;
  onSource: (source: Source) => void;
}) {
  const desk = useDesk(source);
  const {
    snapshot,
    client,
    busy,
    connected,
    readError,
    actionError,
    notice,
    retry,
    intentError,
  } = desk;
  const [selectedId, setSelectedId] = useState("");
  const [notes, setNotes] = useState(DEFAULT_NOTES);
  const [notesDirty, setNotesDirty] = useState(false);
  const [mode, setMode] = useState<RunMode>(() => readRunMode(source));
  const changeMode = (next: RunMode) => setMode(saveRunMode(source, next));
  const [cancelling, setCancelling] = useState(false);
  const [executeSent, setExecuteSent] = useState<string | null>(() => {
    try {
      return sessionStorage.getItem("cuepilot.execute-requested");
    } catch {
      // The cue-state guard keeps writes disabled if storage cannot be read.
      return null;
    }
  });
  const show = snapshot?.show;
  const run = snapshot?.run;
  useEffect(() => {
    if (!selectedId && snapshot) {
      setSelectedId(
        snapshot.run?.speakerId ?? snapshot.show.speakers[0]?.id ?? "",
      );
    }
  }, [snapshot, selectedId]);

  const plan = run?.plan;
  const stage = snapshot?.stage ?? null;
  const selected =
    show?.speakers.find((s) => s.id === selectedId) ?? show?.speakers[0];
  const asset = show?.assets.find(
    (a) => a.id === selected?.presentationAssetId,
  );
  const runSpeaker = show?.speakers.find((s) => s.id === run?.speakerId);
  const nextCue = plan?.cues.find((c) => c.index === run?.nextStep);
  const stalePlan = Boolean(
    plan && show && unfinishedRun(run) && plan.showRevision !== show.revision,
  );
  const preparing =
    run?.status === "needs_approval" && !preparationVerified(run);
  const pendingRun =
    desk.runs.find(unfinishedRun) ?? (unfinishedRun(run) ? run : null);
  const completion = liveCompletion(run);
  const liveCuesCompleted =
    run?.executionMode === "live" && completion.physicalCompleted;
  const canCancel = Boolean(
    run && !["completed", "blocked", "failed"].includes(run.status),
  );
  const disabled = busy || !connected || !!intentError;
  const approved = Boolean(run && ["approved", "running"].includes(run.status));
  const canApprove =
    run?.status === "needs_approval" && plan && !stalePlan && !preparing;
  const canAdvance = Boolean(
    run?.executionMode === "practice" && approved && nextCue && !stalePlan,
  );
  const canExecute =
    run?.executionMode === "live" &&
    run.status === "approved" &&
    plan?.origin === "sponsor" &&
    preparationVerified(run) &&
    !stalePlan &&
    executeSent !== run.id;
  const guidance = cueGuidance(run, stage, {
    retry: !!retry,
    stalePlan,
    executeRequested: run?.id === executeSent,
  });
  const stageLabel = stage
    ? {
        holding: "Holding",
        intro: "Introduction",
        presentation: "Presentation",
      }[stage.scene]
    : "Waiting for stage";
  const stageUrl = source === "fixture" ? "/stage?source=fixture" : "/stage";
  const createRun = () =>
    selected &&
    !pendingRun &&
    desk.act(() =>
      client.createRun(
        selected.id,
        mode,
        mode === "live" && !notesDirty ? undefined : notes.trim(),
      ),
    );
  const execute = () => {
    if (!run) return;
    void desk.act(async () => {
      try {
        sessionStorage.setItem("cuepilot.execute-requested", run.id);
      } catch {
        throw new ClientError(
          "Browser storage is unavailable. No execution request was sent. Restore storage before retrying.",
          "EXECUTION_STORAGE",
        );
      }
      setExecuteSent(run.id);
      try {
        return await client.execute(run.id);
      } catch (error) {
        if (!(error instanceof ClientError && error.uncertain)) {
          setExecuteSent(null);
          try {
            sessionStorage.removeItem("cuepilot.execute-requested");
          } catch {
            // Preserve the original rejection if storage cleanup is denied.
          }
        }
        throw error;
      }
    });
  };
  const cancel = () => {
    if (!run) return;
    void desk.act(async () => {
      setCancelling(true);
      try {
        return await client.cancel(run.id);
      } finally {
        setCancelling(false);
      }
    });
  };
  return (
    <div className="app-shell">
      <a className="skip-link" href="#desk-controls">
        Skip to operator controls
      </a>
      <header className="topbar">
        <a className="brand" href="/" aria-label="CuePilot operator desk">
          <span className="brand-mark">
            <Radio size={21} />
          </span>
          CuePilot
          <span className="brand-divider" />{" "}
          <span className="brand-context">DIRECTOR</span>
        </a>
        <div className="topbar-actions">
          <label className="source-picker">
            <span>DATA SOURCE</span>
            <select
              value={source}
              disabled={busy || !!retry}
              onChange={(e) => onSource(e.target.value as Source)}
              aria-label="Data source"
            >
              <option value="api">Local API</option>
              <option value="fixture">Fixture data</option>
            </select>
          </label>
          <a
            className="button secondary output-link"
            href={stageUrl}
            target="_blank"
            rel="noreferrer"
          >
            <Monitor size={15} /> Open stage <ArrowUpRight size={14} />
          </a>
        </div>
      </header>
      <div
        className={`source-banner ${source === "fixture" ? "is-fixture" : ""}`}
      >
        <span>
          {source === "fixture" ? (
            <Layers3 size={14} />
          ) : connected ? (
            <Wifi size={14} />
          ) : (
            <WifiOff size={14} />
          )}
          {source === "fixture"
            ? "Fixture data"
            : connected
              ? "API connected"
              : "API unavailable"}
        </span>
        <p>
          {source === "fixture"
            ? "Local UI rehearsal. Simulated results; no sponsor services have executed."
            : "Stage changes and action results come from the backend."}
        </p>
        <span className="poll-label">STATE POLL · 500 MS</span>
      </div>
      <main className="desk-main">
        <div className="page-heading">
          <div>
            <p className="eyebrow">ONE STAGE. EVERY CUE.</p>
            <h1>
              Operator desk<span className="heading-dot">.</span>
            </h1>
            <p className="page-subtitle">
              {show?.title ?? "From rehearsal to recall"}
            </p>
          </div>
          <div className="show-meta">
            <span className="eyebrow">STAGE STATUS</span>
            <strong>{stageLabel}</strong>
            <span>
              {plan && run
                ? `${run.receipts.length} / ${plan.cues.length} cues accepted in viewed run`
                : "No cue sequence selected"}
            </span>
            <small title="The configuration revision changes when show setup is edited; it is not a cue count.">
              Configuration revision {show?.revision ?? "—"} · setup changes
            </small>
          </div>
        </div>
        {readError && (
          <div className="callout error" role="alert">
            <WifiOff size={18} />
            <div>
              <strong>Connection unavailable</strong>
              <p>{readError}</p>
            </div>
            <button
              className="text-button"
              onClick={() => void desk.refresh()}
              disabled={busy}
            >
              <RefreshCw size={14} /> Reconnect
            </button>
          </div>
        )}
        {intentError && (
          <div className="callout error" role="alert">
            <TriangleAlert size={18} />
            <div>
              <strong>Saved cue needs review</strong>
              <p>{intentError}</p>
            </div>
          </div>
        )}
        <div className="desk-grid">
          <aside className="panel setup-panel" id="desk-controls">
            <div className="panel-heading">
              <span className="section-number">01</span>
              <h2>Set the segment</h2>
            </div>
            <div className="setup-content">
              <fieldset disabled={disabled || !!retry}>
                <legend className="field-label">
                  Next speaker <span>FOR THE NEXT PLAN</span>
                </legend>
                <div className="speakers">
                  {show ? (
                    show.speakers.map((speaker, index) => (
                      <label
                        className={`speaker-card ${speaker.id === selected?.id ? "selected" : ""}`}
                        key={speaker.id}
                      >
                        <input
                          type="radio"
                          name="speaker"
                          value={speaker.id}
                          checked={speaker.id === selected?.id}
                          onChange={() => setSelectedId(speaker.id)}
                        />
                        <span className="speaker-avatar">
                          {speaker.name
                            .split(" ")
                            .map((n) => n[0])
                            .join("")}
                        </span>
                        <span className="speaker-text">
                          <strong>{speaker.name}</strong>
                          <small>{speaker.title}</small>
                        </span>
                        <span className="speaker-number">0{index + 1}</span>
                      </label>
                    ))
                  ) : (
                    <div className="empty-inline">
                      Speakers will appear when show state is available.
                    </div>
                  )}
                </div>
              </fieldset>
              <div className="notes-field">
                <label className="field-label" htmlFor="notes">
                  Production notes <FileText size={14} />
                </label>
                <textarea
                  id="notes"
                  value={notes}
                  onChange={(e) => {
                    setNotes(e.target.value);
                    setNotesDirty(true);
                  }}
                  maxLength={10000}
                  disabled={busy || !!retry}
                  rows={5}
                />
                <span className="field-hint">
                  {mode === "live" && !notesDirty
                    ? "Live runs keep the backend's current production notes unless you edit this field."
                    : "Included when you create the next run."}
                </span>
              </div>
              <fieldset className="mode-field" disabled={busy || !!retry}>
                <legend className="field-label">New run mode</legend>
                <div className="segmented">
                  <label className={mode === "practice" ? "active" : ""}>
                    <input
                      type="radio"
                      name="mode"
                      value="practice"
                      checked={mode === "practice"}
                      onChange={() => changeMode("practice")}
                    />
                    <Square size={13} /> Practice
                  </label>
                  <label className={mode === "live" ? "active" : ""}>
                    <input
                      type="radio"
                      name="mode"
                      value="live"
                      checked={mode === "live"}
                      onChange={() => changeMode("live")}
                      disabled={source === "fixture"}
                    />
                    <Radio size={14} /> Live
                  </label>
                </div>
                <p className="mode-help">
                  {source === "fixture"
                    ? "Fixture data only supports Practice. Select Local API for Live."
                    : mode === "practice"
                      ? "A fixture plan with operator-controlled cues. This choice is saved for Local API."
                      : "Uses sponsor services after you create and approve a plan, then execute. This choice is saved for Local API."}
                </p>
              </fieldset>
              <button
                className="button primary create-button"
                disabled={
                  disabled ||
                  !selected ||
                  !notes.trim() ||
                  !!retry ||
                  !!pendingRun ||
                  (source === "fixture" && mode === "live")
                }
                onClick={() => void createRun()}
              >
                {busy ? (
                  <LoaderCircle className="spin" size={16} />
                ) : (
                  <Layers3 size={16} />
                )}
                {run?.status === "blocked" || stalePlan
                  ? "Create recovery plan"
                  : mode === "practice"
                    ? "Create practice run"
                    : "Create live run"}
                <ArrowRight size={16} />
              </button>
              {source === "fixture" && mode === "live" && (
                <p className="field-hint">
                  Select Local API to create a live run.
                </p>
              )}
              {pendingRun && (
                <p className="field-hint">
                  Finish or cancel the unfinished run before creating another.
                  {pendingRun.id !== run?.id &&
                    " Select it in Run history to continue."}
                </p>
              )}
            </div>
          </aside>
          <div className="program-column">
            <section className="preview-section">
              <div className="section-heading">
                <h2>
                  <span className="section-number">02</span> Program preview
                </h2>
                <a
                  href={stageUrl}
                  target="_blank"
                  rel="noreferrer"
                  aria-label="Open full-screen stage"
                >
                  <Expand size={16} />
                </a>
              </div>
              <Program stage={stage} source={source} connected={connected} />
              <div className="preview-caption">
                <span>
                  <span
                    className={`connection-dot ${connected ? "connected" : ""}`}
                  />
                  {connected
                    ? source === "api"
                      ? "Reading API stage"
                      : "Reading shared fixture state"
                    : "Waiting for connection"}
                </span>
                <span>STAGE REV {stage?.revision ?? "—"}</span>
              </div>
              <div className="cue-control">
                <div>
                  <span className="eyebrow">CUE CONTROL</span>
                  {run && (
                    <span className="run-mode-badge">
                      Viewing{" "}
                      {run.executionMode === "live" ? "Live" : "Practice"} run
                    </span>
                  )}
                  <strong>{guidance.title}</strong>
                  <p className="operator-guidance">{guidance.detail}</p>
                </div>
                {retry ? (
                  <button
                    className="button primary"
                    disabled={disabled}
                    onClick={() => void desk.advance()}
                  >
                    <RefreshCw size={16} /> Retry same cue
                  </button>
                ) : canExecute ? (
                  <button
                    className="button primary"
                    disabled={disabled}
                    onClick={execute}
                  >
                    <Radio size={16} />
                    Execute live sequence
                  </button>
                ) : canAdvance && nextCue ? (
                  <button
                    className="button primary"
                    disabled={disabled}
                    onClick={() => void desk.advance()}
                  >
                    <ArrowRight size={16} /> {cueLabels[nextCue.scene]}
                  </button>
                ) : run?.status === "needs_approval" ||
                  (stalePlan && unfinishedRun(run)) ? (
                  <a className="button secondary" href="#plan-review">
                    Review plan <ArrowRight size={16} />
                  </a>
                ) : !unfinishedRun(run) ? (
                  <a className="button secondary" href="#desk-controls">
                    {run?.status === "blocked" || run?.status === "failed"
                      ? "Set up recovery run"
                      : "Set up next segment"}{" "}
                    <ArrowRight size={16} />
                  </a>
                ) : null}
              </div>
            </section>
            <section className="panel plan-panel" id="plan-review">
              <div className="panel-heading">
                <span className="section-number">03</span>
                <h2>Review & direct</h2>
                <span
                  className={`status-chip status-${liveCuesCompleted && !completion.fullyVerified ? "running" : (run?.status ?? "idle")}`}
                >
                  {preparing
                    ? "Finalizing plan"
                    : liveCuesCompleted
                      ? completion.fullyVerified
                        ? "live verified"
                        : "cues completed"
                      : (run?.status.replaceAll("_", " ") ?? "No run")}
                </span>
              </div>
              <div className="plan-content">
                <label className="field-label" htmlFor="run-history">
                  Run history <span>VIEW A RUN AND ITS RECEIPTS</span>
                </label>
                <select
                  id="run-history"
                  aria-label="Run history"
                  className="run-history-picker"
                  value={run?.id ?? ""}
                  disabled={busy || !!retry || !connected || !desk.runs.length}
                  onChange={(event) => void desk.selectRun(event.target.value)}
                >
                  {!desk.runs.length && <option value="">No runs yet</option>}
                  {desk.runs.map((entry) => (
                    <option key={entry.id} value={entry.id}>
                      {show?.speakers.find(
                        (speaker) => speaker.id === entry.speakerId,
                      )?.name ?? entry.speakerId}{" "}
                      · {entry.executionMode === "live" ? "Live" : "Practice"} ·{" "}
                      {entry.status.replaceAll("_", " ")} ·{" "}
                      {new Date(entry.createdAt).toLocaleString()} ·{" "}
                      {entry.id.slice(0, 8)}
                    </option>
                  ))}
                </select>
                <p className="field-hint">
                  Viewing this run's plan and receipts. Program preview always
                  shows the current stage.
                </p>
                {desk.historyError && (
                  <p className="field-hint" role="status">
                    Run history unavailable: {desk.historyError}
                  </p>
                )}
                {plan && run ? (
                  <>
                    <div className="plan-summary">
                      <div>
                        <strong>{runSpeaker?.name ?? run.speakerId}</strong>
                        <p>
                          {run.executionMode === "practice"
                            ? "Local UI rehearsal"
                            : "Live execution"}{" "}
                          <span>·</span>{" "}
                          {plan.origin === "fixture"
                            ? "Fixture plan"
                            : "Sponsor plan"}
                        </p>
                      </div>
                      <span className="cue-total">
                        {run.receipts.length}
                        <span> / {plan.cues.length} cues accepted</span>
                      </span>
                    </div>
                    <ol className="cue-list">
                      {plan.cues.map((cue) => {
                        const receipt = run.receipts.find(
                          (r) => r.stepIndex === cue.index,
                        );
                        const next = nextCue?.index === cue.index;
                        return (
                          <li
                            key={cue.index}
                            className={`${receipt ? "cue-done" : ""} ${next ? "cue-next" : ""}`}
                          >
                            <span className="cue-marker">
                              {receipt ? (
                                <Check size={15} />
                              ) : (
                                String(cue.index + 1).padStart(2, "0")
                              )}
                            </span>
                            <div>
                              <strong>{cueLabels[cue.scene]}</strong>
                              <p>{cueDetails[cue.scene]}</p>
                            </div>
                            <span className="cue-state">
                              {receipt ? "ACCEPTED" : next ? "NEXT" : "QUEUED"}
                            </span>
                            {next && <ChevronRight size={16} />}
                          </li>
                        );
                      })}
                    </ol>
                    <details className="plan-details">
                      <summary>
                        Plan details{" "}
                        <span>
                          REV {plan.showRevision} · v{plan.recipeVersion}
                        </span>
                      </summary>
                      <dl>
                        <dt>Plan hash</dt>
                        <dd className="mono">{plan.hash}</dd>
                        <dt>Recipe</dt>
                        <dd>{plan.recipeId}</dd>
                        <dt>Run</dt>
                        <dd className="mono">{run.id}</dd>
                        <dt>Production notes in this plan</dt>
                        <dd>{run.notes}</dd>
                      </dl>
                    </details>
                  </>
                ) : (
                  <div className="empty-plan">
                    <span className="empty-icon">
                      <Layers3 size={25} />
                    </span>
                    <h3>
                      {run?.status === "queued"
                        ? "Preparing the plan"
                        : "Your next segment starts here"}
                    </h3>
                    <p>
                      {run?.status === "queued"
                        ? "Waiting for the backend to return a plan and its evidence."
                        : "Choose a speaker and create a run to review the cue sequence before it reaches the stage."}
                    </p>
                  </div>
                )}
                {liveCuesCompleted && (
                  <div
                    className={`callout ${completion.fullyVerified ? "" : "warning"}`}
                    role="status"
                  >
                    {completion.fullyVerified ? (
                      <ShieldCheck size={17} />
                    ) : (
                      <TriangleAlert size={17} />
                    )}
                    <div>
                      <strong>
                        {completion.fullyVerified
                          ? "Live execution verified"
                          : "Physical cues completed; live verification incomplete"}
                      </strong>
                      <p>
                        Rote execution and Hydra outcome:{" "}
                        {completion.verifiedCompletion
                          ? "verified"
                          : "not yet verified"}
                        .
                      </p>
                      <p>
                        Final RocketRide execution trace:{" "}
                        {completion.rocketrideStatus ?? "not returned yet"}.
                      </p>
                    </div>
                  </div>
                )}
                {run?.reason && (
                  <div className="callout warning">
                    <TriangleAlert size={17} />
                    <div>
                      <strong>Run {run.status}</strong>
                      <p>{run.reason}</p>
                      {run.status === "blocked" && (
                        <p>
                          {run.executionMode === "live"
                            ? "Resolve the returned sponsor setup issue, then create a new live run."
                            : "Restore readiness, then create and approve a new plan."}
                        </p>
                      )}
                    </div>
                  </div>
                )}
                {stalePlan && run?.status !== "blocked" && (
                  <div className="callout warning">
                    <TriangleAlert size={17} />
                    <div>
                      <strong>Show changed after planning</strong>
                      <p>
                        This plan uses revision {plan?.showRevision}; the
                        current show is revision {show?.revision}. Create and
                        approve a new plan.
                      </p>
                    </div>
                  </div>
                )}
                {retry && (
                  <div className="callout warning">
                    <RefreshCw size={17} />
                    <div>
                      <strong>Cue outcome not yet reconciled</strong>
                      <p>
                        Retry the same cue request to retrieve its receipt
                        safely. Other mutations are paused.
                      </p>
                      <code>{retry.requestId}</code>
                    </div>
                  </div>
                )}
                <div className="plan-actions">
                  {run?.status === "needs_approval" && (
                    <button
                      className="button secondary"
                      disabled={disabled || !canApprove || !!retry}
                      onClick={() =>
                        run &&
                        plan &&
                        void desk.act(() => client.approve(run.id, plan.hash))
                      }
                    >
                      <Check size={16} /> Approve plan
                    </button>
                  )}
                  {canCancel && (
                    <button
                      className="button secondary"
                      disabled={disabled || !canCancel || !!retry}
                      onClick={cancel}
                    >
                      {cancelling ? (
                        <LoaderCircle className="spin" size={16} />
                      ) : (
                        <Square size={16} />
                      )}
                      {cancelling ? "Cancelling…" : "Cancel run"}
                    </button>
                  )}
                </div>
                {cancelling && (
                  <p className="field-hint" role="status">
                    Waiting for the backend to stop the driver and confirm the
                    run state.
                  </p>
                )}
                <p className="approval-note">
                  <ShieldCheck size={13} />
                  {run?.status === "completed"
                    ? "All returned cue receipts are available in the execution trace."
                    : `Approval is bound to the exact returned plan. The ${source === "api" ? "backend" : "fixture adapter"} decides cue order.`}
                </p>
              </div>
            </section>
          </div>
          <aside className="panel trace-panel">
            <div className="panel-heading">
              <span className="section-number">04</span>
              <h2>Execution trace</h2>
            </div>
            <div className="readiness-box">
              <div className="readiness-heading">
                <ShieldCheck size={15} />
                <h3>Readiness check</h3>
                <span>JUDGE CONTROL</span>
              </div>
              <div className="toggle-row">
                <div>
                  <strong>Presentation</strong>
                  <small>{asset?.title ?? "No presentation loaded"}</small>
                </div>
                <button
                  type="button"
                  role="switch"
                  aria-checked={asset?.status === "ready"}
                  aria-label="Presentation ready"
                  className={`toggle ${asset?.status === "ready" ? "on" : ""}`}
                  disabled={disabled || !asset || !show || !!retry}
                  onClick={() =>
                    asset &&
                    show &&
                    void desk.act(() =>
                      client.setAssetStatus(
                        asset.id,
                        asset.status === "ready" ? "missing" : "ready",
                        show.revision,
                      ),
                    )
                  }
                >
                  <span />
                </button>
              </div>
              <div
                className={`readiness-state ${asset?.status === "missing" ? "missing" : ""}`}
              >
                {asset?.status === "ready" ? (
                  <Check size={13} />
                ) : (
                  <TriangleAlert size={13} />
                )}
                {asset
                  ? asset.status === "ready"
                    ? "Ready for its cue"
                    : "Missing — a new approved plan is required"
                  : "Waiting for show state"}
              </div>
              <details className="speaker-readiness">
                <summary>Speaker readiness</summary>
                <div className="toggle-row">
                  <span>{selected?.name ?? "No speaker"}</span>
                  <button
                    type="button"
                    role="switch"
                    aria-checked={selected?.ready ?? false}
                    aria-label="Speaker ready"
                    className={`toggle ${selected?.ready ? "on" : ""}`}
                    disabled={disabled || !selected || !show || !!retry}
                    onClick={() =>
                      selected &&
                      show &&
                      void desk.act(() =>
                        client.setSpeakerReady(
                          selected.id,
                          !selected.ready,
                          show.revision,
                        ),
                      )
                    }
                  >
                    <span />
                  </button>
                </div>
              </details>
            </div>
            <div className="trace-content">
              <div className="trace-source">
                <span className="eyebrow">EVIDENCE SOURCE</span>
                <strong>
                  {source === "fixture"
                    ? "Fixture adapter"
                    : "Backend responses"}
                </strong>
                <p>
                  {source === "fixture"
                    ? "Simulated evidence for interface rehearsal."
                    : "Only returned evidence and committed receipts appear here."}
                </p>
              </div>
              {run ? (
                <>
                  <div className="trace-section-label">
                    PROVIDER EVIDENCE <span>{run.traces.length}</span>
                  </div>
                  {run.traces.length ? (
                    run.traces.map((entry, i) => (
                      <EvidenceRow entry={entry} key={`${run.id}-${i}`} />
                    ))
                  ) : (
                    <p className="trace-empty">
                      No provider evidence returned yet.
                    </p>
                  )}
                  <div className="trace-section-label receipts-label">
                    CUE RECEIPTS <span>{run.receipts.length}</span>
                  </div>
                  {run.receipts.length ? (
                    run.receipts.map((receipt) => (
                      <div className="receipt" key={receipt.id}>
                        <span className="trace-icon">
                          <Check size={14} />
                        </span>
                        <div>
                          <strong>{cueLabels[receipt.scene]}</strong>
                          <p>
                            {time(receipt.committedAt)}{" "}
                            <span>· Stage rev {receipt.stageRevision}</span>
                          </p>
                          <details>
                            <summary>Inspect receipt</summary>
                            <pre>{JSON.stringify(receipt, null, 2)}</pre>
                          </details>
                        </div>
                      </div>
                    ))
                  ) : (
                    <p className="trace-empty">No cues have been accepted.</p>
                  )}
                </>
              ) : (
                <div className="trace-placeholder">
                  <Flag size={23} />
                  <p>Ready when you are.</p>
                  <span>The trace begins with your first run.</span>
                </div>
              )}
            </div>
            <div className="trace-footer">
              <CircleHelp size={15} />
              <p>
                Practice uses a fixture plan. Sponsor execution is only verified
                by returned live evidence.
              </p>
            </div>
          </aside>
        </div>
        <div className="action-feedback" aria-live="polite" aria-atomic="true">
          {actionError ? (
            <div className="callout error">
              <TriangleAlert size={18} />
              <div>
                <strong>
                  {source === "fixture"
                    ? "Fixture action rejected"
                    : "Action could not complete"}
                </strong>
                <p>{actionError}</p>
              </div>
            </div>
          ) : notice ? (
            <div className="callout success">
              <Check size={18} />
              <p>{notice}</p>
            </div>
          ) : busy ? (
            <p>
              <LoaderCircle className="spin" size={15} /> Waiting for the{" "}
              {source === "api" ? "API" : "fixture adapter"} response…
            </p>
          ) : null}
        </div>
        <footer className="desk-footer">
          <span>
            <Radio size={13} /> CUEPILOT{" "}
            <span className="footer-separator">/</span> REHEARSE. APPROVE.
            DIRECT.
          </span>
          <span>
            One segment <i /> Three speakers <i /> One stage
          </span>
        </footer>
      </main>
    </div>
  );
}
function StagePage({ source }: { source: Source }) {
  const { stage, connected, loading } = useStage(source);
  useEffect(() => {
    document.title = "CuePilot · Program output";
  }, []);
  return (
    <main className="stage-page">
      <Program
        stage={stage}
        source={source}
        connected={connected}
        loading={loading}
        full
      />
    </main>
  );
}
export default function App() {
  const [source, setSource] = useState<Source>(initialSource);
  const changeSource = (next: Source) => {
    const url = new URL(location.href);
    if (next === "fixture") url.searchParams.set("source", "fixture");
    else url.searchParams.delete("source");
    history.replaceState(null, "", url);
    setSource(next);
  };
  return location.pathname === "/stage" ? (
    <StagePage key={source} source={source} />
  ) : (
    <Desk key={source} source={source} onSource={changeSource} />
  );
}
