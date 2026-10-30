"use client";

import { useEffect, useRef, useState } from "react";
import { api, getToken } from "./api";

export type LiveEvent =
  | { type: "hello"; user_id: string; role: string }
  | { type: "ping" }
  | { type: "call.queued"; call_id: number; lead_id: number; phone: string }
  | { type: "call.turn"; call_id: number; turn_index: number; role: "user" | "assistant"; text: string; latency_ms?: number }
  | { type: "call.ended"; call_id: number; duration_s: number; outcome: string; ttfa_p50_ms?: number | null; recording_path?: string | null }
  | { type: "lead.created"; id: number; phone: string; name?: string | null }
  | { type: "lead.updated"; id: number }
  | { type: "lead.deleted"; id: number }
  | { type: "campaign.status"; id: number; status: string }
  | { type: string; [k: string]: unknown }; // fallback for forward-compat

type Status = "connecting" | "open" | "closed" | "error";

/**
 * Persistent WebSocket to /api/live with auto-reconnect + exponential backoff.
 * Pass an event handler; it fires for every incoming event (including hello/ping).
 */
export function useLiveEvents(onEvent: (e: LiveEvent) => void) {
  const [status, setStatus] = useState<Status>("connecting");
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectRef = useRef(0);
  const handlerRef = useRef(onEvent);
  // Keep handler ref fresh without resubscribing the WS each render.
  useEffect(() => {
    handlerRef.current = onEvent;
  }, [onEvent]);

  useEffect(() => {
    let stopped = false;
    let backoffTimer: ReturnType<typeof setTimeout> | null = null;

    const connect = () => {
      const token = getToken();
      if (!token) {
        setStatus("closed");
        return;
      }
      // Derive WS URL from API base (http -> ws, https -> wss).
      const url = api.base.replace(/^http/, "ws") + "/api/live?token=" + encodeURIComponent(token);
      setStatus("connecting");
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => {
        reconnectRef.current = 0;
        setStatus("open");
      };
      ws.onmessage = (ev) => {
        try {
          const event = JSON.parse(ev.data) as LiveEvent;
          handlerRef.current(event);
        } catch {
          /* ignore non-JSON */
        }
      };
      ws.onerror = () => {
        setStatus("error");
      };
      ws.onclose = () => {
        setStatus("closed");
        if (stopped) return;
        // Exponential backoff: 1s, 2s, 4s, ... up to 30s.
        const delay = Math.min(30000, 1000 * 2 ** reconnectRef.current);
        reconnectRef.current += 1;
        backoffTimer = setTimeout(connect, delay);
      };
    };

    connect();

    return () => {
      stopped = true;
      if (backoffTimer) clearTimeout(backoffTimer);
      wsRef.current?.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return { status };
}
