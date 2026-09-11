import { useEffect, useMemo, useState } from "react";
import { getClient } from "./api-client";
import type { Source, Stage } from "./model";

export function useStage(source: Source) {
  const client = useMemo(() => getClient(source), [source]);
  const [stage, setStage] = useState<Stage | null>(null);
  const [connected, setConnected] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    let reading = false;
    setStage(null);
    setConnected(false);
    setLoading(true);

    const poll = async () => {
      if (reading) return;
      reading = true;
      try {
        const next = await client.readStage();
        if (!active) return;
        setStage(next);
        setConnected(true);
      } catch {
        // Retain the last output with an explicit connection warning.
        if (active) setConnected(false);
      } finally {
        reading = false;
        if (active) setLoading(false);
      }
    };

    void poll();
    const interval = window.setInterval(() => void poll(), 500);
    return () => {
      active = false;
      window.clearInterval(interval);
    };
  }, [client]);

  return { stage, connected, loading };
}
