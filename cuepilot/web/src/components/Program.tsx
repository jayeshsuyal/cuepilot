import { ArrowUpRight, Radio } from "lucide-react";
import type { Source, Stage } from "../lib/model";
export function Program({
  stage,
  source,
  connected,
  full = false,
}: {
  stage: Stage | null;
  source: Source;
  connected: boolean;
  full?: boolean;
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
          {!connected ? "SIGNAL UNAVAILABLE" : onProgram ? "LIVE" : "HOLDING"}
        </span>
      </div>
      <div className="program-body">
        {!stage ? (
          <>
            <span className="program-kicker">AWAITING PROGRAM</span>
            <Heading>Stand by.</Heading>
            <p>
              Waiting for stage state from the{" "}
              {source === "api" ? "API" : "fixture adapter"}.
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
            {stage.reason && <p className="program-reason">{stage.reason}</p>}
          </>
        )}
      </div>
      <div className="program-bottom">
        <span>
          {source === "fixture"
            ? "FIXTURE DATA · LOCAL UI REHEARSAL"
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
