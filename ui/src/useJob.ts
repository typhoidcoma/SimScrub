import { useEffect, useRef, useState } from "react";
import type { Job } from "./types";

/** Live job manifest pushed by the server after every change. */
export function useJob() {
  const [job, setJob] = useState<Job | null>(null);
  const [online, setOnline] = useState(false);
  const retry = useRef(0);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let timer: number | undefined;
    let closed = false;

    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/ws`);
      ws.onopen = () => { setOnline(true); retry.current = 0; };
      ws.onmessage = (e) => {
        const msg = JSON.parse(e.data);
        if (msg.type === "job") setJob(msg.job);
      };
      ws.onclose = () => {
        setOnline(false);
        if (closed) return;
        const wait = Math.min(4000, 400 * 2 ** retry.current++);
        timer = window.setTimeout(connect, wait);
      };
    };
    connect();
    return () => { closed = true; window.clearTimeout(timer); ws?.close(); };
  }, []);

  return { job, online };
}

export async function api(path: string, method = "POST") {
  const r = await fetch(path, { method });
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new Error(body.detail ?? `Request failed (${r.status})`);
  }
  return r.json();
}
