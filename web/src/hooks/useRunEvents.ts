import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { apiUrl } from "../api/client";
import type { AgentRun } from "../api/learning";

const closedStatuses = new Set(["completed", "failed", "cancelled", "expired", "blocked"]);
// After this many failures without a message the stream is abandoned and polling continues.
const maxFailures = 3;

/**
 * Subscribe to a run's progress stream and write each pushed state into the query cache.
 * Returns whether the stream is live so callers can slow their safety polling.
 */
export function useRunEvents(runId: string, enabled: boolean): boolean {
  const cache = useQueryClient();
  const [connected, setConnected] = useState(false);
  useEffect(() => {
    if (!enabled || typeof EventSource === "undefined") return;
    let failures = 0;
    const source = new EventSource(apiUrl(`/api/v1/agent-runs/${runId}/events`), { withCredentials: true });
    const stop = () => { source.close(); setConnected(false); };
    source.addEventListener("open", () => setConnected(true));
    source.addEventListener("run", (event) => {
      failures = 0;
      setConnected(true);
      const run = JSON.parse((event as MessageEvent<string>).data) as AgentRun;
      cache.setQueryData(["agent-run", runId], run);
      // The server ends closed streams; closing here stops EventSource from reconnecting.
      if (closedStatuses.has(run.status)) stop();
    });
    source.addEventListener("error", () => {
      setConnected(false);
      failures += 1;
      if (failures >= maxFailures) source.close();
    });
    return stop;
  }, [cache, enabled, runId]);
  return connected;
}
