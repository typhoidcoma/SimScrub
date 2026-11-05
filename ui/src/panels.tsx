import { useEffect, useRef, useState } from "react";
import { Panel, StatusDot } from "./components";
import type { Asset, Dims, Job } from "./types";
import { fileUrl } from "./types";

const cm = (d: Dims) => `${d.l.toFixed(0)}×${d.w.toFixed(0)}×${d.h.toFixed(0)}`;

/* ---------------- 01 CAPTURE ---------------- */
export type Source = { file: File } | { path: string } | null;

export function CapturePanel({
  job, busy, onProcess,
}: { job: Job | null; busy: boolean; onProcess: (src: Source) => void }) {
  const st = job?.stages.capture;
  const [rows, setRows] = useState<string[][]>([["", "", ""], ["", "", ""], ["", "", ""]]);
  const [source, setSource] = useState<Source>(null);
  const [over, setOver] = useState(false);
  const [videos, setVideos] = useState<string[]>([]);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    fetch("/api/videos").then((r) => r.json()).then(setVideos).catch(() => setVideos([]));
  }, []);

  const pickedName = source ? ("file" in source ? source.file.name : source.path) : null;
  const running = st?.status === "working";
  const shownName = running ? job?.capture.video ?? pickedName : pickedName ?? job?.capture.video ?? null;
  const info = job?.capture;

  return (
    <Panel id="p-capture" n={1} title="Capture" state={st}>
      <div className="field">
        <span className="label dim">Office walkthrough</span>
        <div
          className={`drop ${shownName ? "has" : ""} ${over ? "over" : ""}`}
          onClick={() => !busy && fileRef.current?.click()}
          onDragOver={(e) => { e.preventDefault(); setOver(true); }}
          onDragLeave={() => setOver(false)}
          onDrop={(e) => {
            e.preventDefault();
            setOver(false);
            const f = e.dataTransfer.files[0];
            if (f) setSource({ file: f });
          }}
          role="button"
          tabIndex={0}
        >
          {shownName ? (
            <>
              <StatusDot s={running ? "working" : pickedName ? "queued" : st?.status ?? "idle"} text="" />
              <div>
                <strong>{shownName}</strong>
                <div className="label dim" style={{ marginTop: 4 }}>
                  {pickedName && !running && pickedName !== job?.capture.video
                    ? "Ready · press Process"
                    : `${info?.frames_extracted ?? 0} frames${info?.duration ? ` · ${info.duration.toFixed(1)}s` : ""}`}
                </div>
              </div>
            </>
          ) : (
            <div>
              <strong>Drop your walkthrough video</strong>
              <div className="label dim" style={{ marginTop: 6 }}>or click to browse · MP4 · MOV</div>
            </div>
          )}
          <input
            ref={fileRef}
            type="file"
            accept="video/*"
            hidden
            onChange={(e) => { const f = e.target.files?.[0]; if (f) setSource({ file: f }); }}
          />
        </div>
        {videos.length > 0 && (
          <div className="row">
            <span className="label dim">In project folder</span>
            {videos.map((v) => (
              <button key={v} className="tag" disabled={busy} onClick={() => setSource({ path: v })}>{v}</button>
            ))}
          </div>
        )}
      </div>

      <div className="field">
        <span className="label dim" title="Tape-measure 2–3 boxes. Everything else is scaled from these.">
          Box measurements · cm · L × W × H
        </span>
        {rows.map((r, i) => (
          <div className="meas" key={i}>
            <span className="label">#{i + 1}</span>
            {r.map((v, j) => (
              <input
                key={j}
                className="input"
                inputMode="decimal"
                placeholder={["L", "W", "H"][j]}
                value={v}
                onChange={(e) => {
                  const next = rows.map((x) => x.slice());
                  next[i][j] = e.target.value;
                  setRows(next);
                }}
              />
            ))}
          </div>
        ))}
      </div>

      <button className="btn btn-primary btn-wide" disabled={busy || !source} onClick={() => onProcess(source)}>
        {busy ? "Processing…" : "Process"}
      </button>
    </Panel>
  );
}

/* ---------------- 02 SEGMENT ---------------- */
export function SegmentPanel({ job, onToggle }: { job: Job | null; onToggle: (id: string) => void }) {
  const st = job?.stages.segment;
  const assets = job?.assets ?? [];
  const kept = assets.filter((a) => !a.excluded).length;
  const res = job?.capture.resolution;
  const frame = job ? fileUrl(job, job.capture.frame) : undefined;
  const [hover, setHover] = useState<string | null>(null);

  return (
    <Panel
      id="p-segment"
      n={2}
      title="Segment"
      state={st}
      className="span-2"
      right={assets.length ? <span className="label">{kept} / {assets.length} kept</span> : undefined}
    >
      <div className="frame-view" style={res ? { aspectRatio: `${res[0]} / ${res[1]}` } : undefined}>
        {frame ? <img src={frame} alt="Walkthrough frame" /> : <div className="frame-empty label dim">Sharpest frame appears here</div>}

        {/* True outlines for boxes visible in the hero frame */}
        <svg className="outlines" viewBox="0 0 1 1" preserveAspectRatio="none">
          {assets.map((a) =>
            a.polygon ? (
              <polygon
                key={a.id}
                className={`ol ${a.excluded ? "off" : ""} ${hover === a.id ? "hot" : ""}`}
                points={a.polygon.map(([x, y]) => `${x},${y}`).join(" ")}
                vectorEffect="non-scaling-stroke"
                onClick={() => onToggle(a.id)}
                onMouseEnter={() => setHover(a.id)}
                onMouseLeave={() => setHover(null)}
              >
                <title>{a.excluded ? `Bring ${a.id} back` : `Exclude ${a.id}`}</title>
              </polygon>
            ) : null,
          )}
        </svg>
        {assets.map((a) => {
          if (!a.polygon) return null;
          const x = Math.min(...a.polygon.map((p) => p[0]));
          const y = Math.min(...a.polygon.map((p) => p[1]));
          return (
            <span key={a.id} className={`ol-num ${a.excluded ? "off" : ""}`} style={{ left: `${x * 100}%`, top: `${Math.max(y, 0.04) * 100}%` }}>
              {a.id.slice(3)}
            </span>
          );
        })}
      </div>

      {/* Every box found anywhere in the video, including ones not in this frame */}
      {assets.some((a) => a.crop) && (
        <div className="strip">
          {assets.map((a) => (
            <button
              key={a.id}
              className={`strip-item ${a.excluded ? "off" : ""} ${hover === a.id ? "hot" : ""}`}
              onClick={() => onToggle(a.id)}
              onMouseEnter={() => setHover(a.id)}
              onMouseLeave={() => setHover(null)}
              title={a.flag ?? (a.excluded ? "Excluded · tap to keep" : "Tap to exclude")}
            >
              <div className="strip-img">{job && a.crop && <img src={fileUrl(job, a.crop)} alt={a.id} />}</div>
              <div className="strip-meta">
                <span className="label">{a.id.slice(3)}</span>
                <span className={`st ${a.excluded ? "attention" : "done"}`}>{a.excluded ? (a.flag ? "Flagged" : "Excluded") : "Kept"}</span>
              </div>
            </button>
          ))}
        </div>
      )}
    </Panel>
  );
}

/* ---------------- 03 RECONSTRUCT ---------------- */
function ReconCard({ job, a, i }: { job: Job; a: Asset; i: number }) {
  const r = a.recon;
  const thumb = fileUrl(job, a.crop ?? a.thumb);
  return (
    <div className={`card ${r.status}`} style={{ animationDelay: `${i * 30}ms` }}>
      <div className="thumb">
        {thumb && <img src={thumb} alt="" />}
        {r.status === "proxy" && <span className="tag orange">Proxy</span>}
        {r.status === "ready" && <span className="tag green">Mesh</span>}
      </div>
      <div className="meta">
        <span className="label">{a.id} · {r.source === "proxy" ? "Fitted cuboid" : "Tripo"}</span>
        <span className="ttl">{a.measured ? `Box ${cm(a.measured)} cm` : "Box · size from calibration"}</span>
        <StatusDot s={r.status} text={r.status === "ready" && r.tris ? `Mesh ready · ${(r.tris / 1000).toFixed(1)}k tris` : undefined} />
        {r.chip && <div className="chip">{r.chip}</div>}
      </div>
    </div>
  );
}

export function ReconstructPanel({ job }: { job: Job | null }) {
  const st = job?.stages.reconstruct;
  const assets = job?.assets.filter((a) => !a.excluded) ?? [];
  const started = st && st.status !== "idle";
  return (
    <Panel id="p-reconstruct" n={3} title="Reconstruct" state={st} className="span-3">
      {started && assets.length ? (
        <div className="cards">{assets.map((a, i) => <ReconCard key={a.id} job={job!} a={a} i={i} />)}</div>
      ) : (
        <div className="empty-note">One card per box. Bad generations swap to a proxy automatically.</div>
      )}
    </Panel>
  );
}

/* ---------------- 04 SIMREADY ---------------- */
export function SimReadyPanel({ job }: { job: Job | null }) {
  const st = job?.stages.simready;
  const assets = job?.assets.filter((a) => !a.excluded && a.checks.length) ?? [];
  const done = st?.status === "done" || st?.status === "attention";
  return (
    <Panel
      id="p-simready"
      n={4}
      title="SimReady"
      state={st}
      className="span-2"
      right={<button className="btn btn-primary btn-sm" disabled={!done}>Export USD</button>}
    >
      {assets.length ? (
        <div className="checks">
          {assets.map((a) => {
            const flag = a.checks.some((c) => c.status === "warn" || c.status === "fail");
            return (
              <div key={a.id} className={`check-card ${flag ? "flag" : ""}`}>
                <div className="row">
                  <span className="label">{a.id}</span>
                  {a.recon.status === "proxy" && <span className="tag orange">Proxy</span>}
                  <span className="spacer" />
                  <span className="label dim">{a.dims ? `${cm(a.dims)} cm` : ""}</span>
                </div>
                {a.checks.map((c) => (
                  <div key={c.rule} className={`check-row ${c.status}`} title={c.detail}>
                    <span className={`dot ${c.status}`} />
                    {c.label}
                    <span className="d">{c.status === "pass" ? "" : c.detail || c.status}</span>
                  </div>
                ))}
              </div>
            );
          })}
        </div>
      ) : (
        <div className="empty-note">Validation checklist per asset: meters, collider, label, material.</div>
      )}
    </Panel>
  );
}

/* ---------------- 05 STAGE ---------------- */
export function StagePanel({ job }: { job: Job | null }) {
  const st = job?.stages.stage;
  const info = job?.stage;
  const ready = st?.status === "done";
  return (
    <Panel id="p-stage" n={5} title="Stage" state={st}>
      <div className="stage-thumb">
        {info?.thumb ? <img src={info.thumb} alt="Assembled stage" /> : <div className="frame-empty label dim">Assembled scene</div>}
        <div className="grid-ov" />
      </div>
      <div className="dims">
        {(["l", "w", "h"] as const).map((k) => (
          <div className="dim" key={k}>
            <span className="label dim">{{ l: "Length", w: "Width", h: "Height" }[k]}</span>
            <b>{info?.room ? (info.room[k] / 100).toFixed(2) : "—"}<small>m</small></b>
          </div>
        ))}
      </div>
      {info?.scale_source && <span className="label dim">{info.scale_source}</span>}
      <button className="btn btn-outline btn-wide" disabled={!ready}>Launch Isaac Sim</button>
    </Panel>
  );
}
