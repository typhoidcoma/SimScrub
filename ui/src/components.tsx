import type { ReactNode } from "react";
import type { Job, StageState, Status } from "./types";
import { STAGES } from "./types";

const STATUS_TEXT: Record<string, string> = {
  idle: "Waiting", working: "Working", done: "Approved", attention: "Needs a look", failed: "Stopped",
  queued: "Queued", ready: "Mesh ready", proxy: "Proxy", pass: "Pass", fixed: "Auto-fixed", warn: "Check", fail: "Fail",
};

export function StatusDot({ s, text }: { s: string; text?: string }) {
  return (
    <span className={`st ${s}`}>
      <span className={`dot ${s}`} />
      {text ?? STATUS_TEXT[s] ?? s}
    </span>
  );
}

export function Header({ online, job }: { online: boolean; job: Job | null }) {
  return (
    <header className="hdr">
      <div className="brand">
        <div className="brand-bar" />
        <div>
          <div className="brand-name">TE SIMSCRUB</div>
          <div className="label dim brand-sub">Production Systems</div>
        </div>
      </div>
      <div className="hdr-right">
        <span className="label">Project {job?.id.split("-")[1] ?? "---"}</span>
        <StatusDot s={online ? "done" : "attention"} text={online ? "Online" : "Offline"} />
      </div>
    </header>
  );
}

const TAB_NAMES: Record<string, string> = {
  capture: "Capture", segment: "Segment", reconstruct: "Reconstruct", simready: "SimReady", stage: "Stage",
};

export function Tabs({ job, active, onPick }: { job: Job | null; active: string; onPick: (s: string) => void }) {
  return (
    <nav className="tabs">
      {STAGES.map((s, i) => {
        const st = job?.stages[s].status ?? "idle";
        return (
          <button key={s} className={`tab ${active === s ? "on" : ""}`} onClick={() => onPick(s)}>
            <span className={`dot ${st}`} />
            0{i + 1} {TAB_NAMES[s]}
          </button>
        );
      })}
    </nav>
  );
}

export function Panel({
  n, title, state, className = "", right, children, id,
}: {
  n: number; title: string; state?: StageState; className?: string; right?: ReactNode; children: ReactNode; id?: string;
}) {
  const s: Status = state?.status ?? "idle";
  return (
    <section id={id} className={`panel ${s} ${className}`}>
      <div className="panel-hd">
        <span className="label"><span className="n">0{n}</span> / {title}</span>
        {right ?? (state && <StatusDot s={s} />)}
      </div>
      {children}
      {state?.message && s !== "idle" && <div className="panel-msg">{state.message}</div>}
    </section>
  );
}

export function ProcessMonitor({ job, showLog, onToggleLog }: { job: Job | null; showLog: boolean; onToggleLog: () => void }) {
  const SEGS = 40;
  const total = job ? STAGES.reduce((a, s) => a + job.stages[s].progress, 0) / STAGES.length : 0;
  const lit = Math.round(total * SEGS);
  const working = job ? STAGES.some((s) => job.stages[s].status === "working") : false;
  const current = job ? STAGES.findIndex((s) => job.stages[s].status === "working") : -1;
  return (
    <div className="monitor">
      <div className="row">
        <span className="label"><span className="n">06</span> / Process Monitor</span>
        <span className="spacer" />
        <button className="tag" onClick={onToggleLog}>{showLog ? "Hide log" : "Show log"}</button>
        <span className="label">
          Stage {String(current >= 0 ? current + 1 : lit === SEGS ? 5 : 0).padStart(2, "0")} / 05 ·{" "}
          {String(Math.round(total * 100)).padStart(3, "0")}%
        </span>
      </div>
      <div className="segs">
        {Array.from({ length: SEGS }, (_, i) => (
          <div key={i} className={`seg ${i < lit ? "on" : ""} ${working && i === lit ? "head" : ""}`} />
        ))}
      </div>
      {showLog && (
        <div className="log">
          {job?.log.length ? (
            [...job.log].reverse().map((l, i) => (
              <div key={i}>
                <span className="t">{l.t.toFixed(1).padStart(5, "0")}s</span>
                <span className="s">{l.stage}</span>
                {l.text}
              </div>
            ))
          ) : (
            <div className="empty-note">No activity yet.</div>
          )}
        </div>
      )}
    </div>
  );
}

export function Footer({ job }: { job: Job | null }) {
  const states = job ? STAGES.map((s) => job.stages[s].status) : [];
  const bad = states.includes("failed");
  const warn = states.includes("attention");
  return (
    <footer className="ftr">
      <span className="label dim">SYS / TE SimScrub v1.0</span>
      <StatusDot
        s={bad ? "failed" : warn ? "attention" : "done"}
        text={bad ? "Stage stopped" : warn ? "Systems nominal · items flagged" : "All systems nominal"}
      />
    </footer>
  );
}
