import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getClient } from "./api-client";
import { readIntent, sendCue } from "./cue-intent";
import type { ActionResult, Source, Snapshot } from "./model";

export function useDesk(source: Source) {
  const client = useMemo(() => getClient(source), [source]);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
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
  const refresh = useCallback(async () => {
    const current = ++sequence.current;
    try {
      const next = await client.read();
      if (sourceRef.current !== source || current !== sequence.current) return;
      setSnapshot(next);
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
      await refresh();
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
  return {
    client,
    snapshot,
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
