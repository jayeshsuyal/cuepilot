import { ArrowUpRight, Radio } from "lucide-react";
import type { Source, Stage } from "../lib/model";
export function Program({
  stage,
  source,
  connected,
  full = false,
  loading = false,
}: {
  stage: Stage | null;
  source: Source;
  connected: boolean;
  full?: boolean;
  loading?: boolean;
}) {
  const Heading = full ? "h1" : "h3";
  const onProgram = stage && stage.scene !== "holding";
  return (
    <section
      className={`program scene-${stage?.scene ?? "unknown"} ${full ? "program-full" : ""}`}
      aria-label="Program output"
    >
      <div className="program-top">
        <span className="program-brand">
          <Radio size={17} /> CUEPILOT
        </span>
        <span className={`signal ${onProgram ? "signal-live" : ""}`}>
          <i />
          {loading
            ? "CONNECTING"
            : !connected
              ? "SIGNAL UNAVAILABLE"
              : onProgram
                ? "ON STAGE"
                : "HOLDING"}
        </span>
      </div>
      <div className="program-body">
        {!stage ? (
          <>
            <span className="program-kicker">
              {loading ? "CONNECTING TO PROGRAM" : "AWAITING PROGRAM"}
            </span>
            <Heading>Stand by.</Heading>
            <p role={!loading && !connected ? "alert" : undefined}>
              {full
                ? loading
                  ? "Connecting to the stage."
                  : "The program will resume when the stage connection is restored."
                : `Waiting for stage state from the ${source === "api" ? "API" : "fixture adapter"}.`}
            </p>
          </>
        ) : (
          <>
            <span className="program-kicker">
              {stage.scene === "holding"
                ? "PLEASE STAND BY"
                : stage.scene === "intro"
                  ? "PLEASE WELCOME"
                  : "THE PRESENTATION"}
            </span>
            <Heading>{stage.title}</Heading>
            <p className="program-subtitle">{stage.subtitle}</p>
            {!full && stage.reason && (
              <p className="program-reason">{stage.reason}</p>
            )}
          </>
        )}
      </div>
      <div className="program-bottom">
        <span>
          {source === "fixture"
            ? "FIXTURE DATA · LOCAL UI REHEARSAL"
            : full
              ? "CUEPILOT"
              : "API STAGE STATE"}
        </span>
        {stage?.scene === "presentation" ? (
          <ArrowUpRight size={28} />
        ) : (
          <span className="program-rule" />
        )}
      </div>
      {!connected && stage && (
        <div className="stale-overlay" role="alert">
          <strong>Connection lost</strong>
          <span>Last known stage · Output is not confirmed current</span>
        </div>
      )}
    </section>
  );
}
