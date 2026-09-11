import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getClient } from "./api-client";
import { readIntent, sendCue } from "./cue-intent";
import type { ActionResult, RunSummary, Source, Snapshot } from "./model";

export function useDesk(source: Source) {
  const client = useMemo(() => getClient(source), [source]);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [historyError, setHistoryError] = useState("");
  const [readError, setReadError] = useState("");
  const [actionError, setActionError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [connected, setConnected] = useState(false);
  const [retry, setRetry] = useState(() => readIntent(sessionStorage, source));
  const gate = useRef(false);
  const sourceRef = useRef(source);
  sourceRef.current = source;
  const sequence = useRef(0);
  const historySequence = useRef(0);
  useEffect(() => {
    if (!notice || busy) return;
    const timeout = window.setTimeout(() => setNotice(""), 5000);
    return () => window.clearTimeout(timeout);
  }, [notice, busy]);
  const refreshRuns = useCallback(async () => {
    const current = ++historySequence.current;
    try {
      const next = await client.listRuns();
      if (sourceRef.current !== source || current !== historySequence.current)
        return;
      setRuns(next);
      setHistoryError("");
    } catch (error) {
      if (sourceRef.current !== source || current !== historySequence.current)
        return;
      setHistoryError(
        error instanceof Error ? error.message : "Unable to read run history.",
      );
    }
  }, [client, source]);
  const refresh = useCallback(async () => {
    const current = ++sequence.current;
    try {
      const next = await client.read();
      if (sourceRef.current !== source || current !== sequence.current) return;
      setSnapshot(next);
      if (next.run) {
        const { id, speakerId, executionMode, status, createdAt, nextStep } =
          next.run;
        const entry = {
          id,
          speakerId,
          executionMode,
          status,
          createdAt,
          nextStep,
        };
        setRuns((previous) =>
          [entry, ...previous.filter((run) => run.id !== entry.id)].sort(
            (a, b) => b.createdAt.localeCompare(a.createdAt),
          ),
        );
      }
      setReadError("");
      setConnected(true);
    } catch (error) {
      if (sourceRef.current !== source || current !== sequence.current) return;
      setReadError(
        error instanceof Error ? error.message : "Unable to read show state.",
      );
      setConnected(false);
    }
  }, [client, source]);
  useEffect(() => {
    setRuns([]);
    setHistoryError("");
    void refreshRuns();
    const interval = window.setInterval(() => void refreshRuns(), 10000);
    return () => {
      clearInterval(interval);
      historySequence.current++;
    };
  }, [refreshRuns]);
  useEffect(() => {
    setSnapshot(null);
    setConnected(false);
    setActionError("");
    setNotice("");
    setRetry(readIntent(sessionStorage, source));
    let reading = false;
    const poll = async () => {
      if (reading || gate.current) return;
      reading = true;
      try {
        await refresh();
      } finally {
        reading = false;
      }
    };
    void poll();
    const interval = window.setInterval(() => void poll(), 500);
    return () => {
      clearInterval(interval);
      sequence.current++;
    };
  }, [refresh]);

  const act = async (fn: () => Promise<ActionResult>) => {
    if (gate.current) return;
    gate.current = true;
    sequence.current++;
    setBusy(true);
    setActionError("");
    setNotice("");
    try {
      const result = await fn();
      if (sourceRef.current === source) setNotice(result.message);
    } catch (error) {
      if (sourceRef.current === source)
        setActionError(
          error instanceof Error ? error.message : "The action failed.",
        );
    } finally {
      await Promise.all([refresh(), refreshRuns()]);
      gate.current = false;
      setBusy(false);
    }
  };
  const advance = () => {
    const run = snapshot?.run;
    if (!run || gate.current) return;
    return act(async () => {
      try {
        return await sendCue(client, run.id, run.nextStep, sessionStorage);
      } finally {
        setRetry(readIntent(sessionStorage, source));
      }
    });
  };
  const selectRun = (runId: string) => {
    if (gate.current || retry) return;
    return act(() => client.selectRun(runId));
  };
  return {
    client,
    snapshot,
    runs,
    historyError,
    refreshRuns,
    selectRun,
    readError,
    actionError,
    notice,
    busy,
    connected,
    retry,
    refresh,
    act,
    advance,
  };
}
