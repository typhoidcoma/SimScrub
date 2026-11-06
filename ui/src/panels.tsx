import type React from "react";
import { useContext, useEffect, useRef, useState } from "react";
import { TabVisible } from "./tabVisible";
import { Panel, StatusDot } from "./components";
import type { Asset, Dims, Job, Step } from "./types";
import { fileUrl } from "./types";

const cm = (d: Dims) => `${d.l.toFixed(0)}×${d.w.toFixed(0)}×${d.h.toFixed(0)}`;

/* ---------------- 01 CAPTURE ---------------- */
export type Source = { file: File } | { path: string } | null;

export function CapturePanel({
  job, busy, onProcess,
}: { job: Job | null; busy: boolean; onProcess: (src: Source) => void }) {
  const st = job?.stages.capture;
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

      <div className="note">
        Tape-measure 2–3 boxes before you film. You'll enter each box's size in SimReady, next to its mesh.
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
      <div
        className="frame-view"
        style={res ? ({ aspectRatio: `${res[0]} / ${res[1]}`, "--ar": res[0] / res[1] } as React.CSSProperties) : undefined}
      >
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
interface Plan {
  boxes: number;
  cached: number;
  to_generate: number;
  credits: number;
  credits_per_model: number;
  balance: number | null;
  chip: string | null;
}

function StepList({ steps, full = false }: { steps: Step[]; full?: boolean }) {
  return (
    <ol className="steps">
      {steps.map((s, i) => (
        <li key={i} className={`step ${s.status} ${s.more ? "has-more" : ""}`} title={!full && s.more ? s.more : undefined}>
          <span className={`dot ${STEP_DOT[s.status]}`} />
          <span className="step-name">{s.name}</span>
          <span className="step-detail">{s.detail}</span>
          {full && s.more && <span className="step-more">{s.more}</span>}
        </li>
      ))}
    </ol>
  );
}

const STEP_DOT: Record<Step["status"], string> = {
  waiting: "idle", working: "working", done: "done", skipped: "fixed", failed: "fail",
};

function MeshView({ src, auto = true, orbit }: { src: string; auto?: boolean; orbit?: string }) {
  const visible = useContext(TabVisible);
  if (!visible) return null;
  return (
    <model-viewer
      className="mv-fill"
      src={src}
      camera-controls
      auto-rotate={auto || undefined}
      auto-rotate-delay="2500"
      rotation-per-second="20deg"
      interaction-prompt="none"
      shadow-intensity="0.7"
      exposure="1.1"
      title="Drag to orbit · scroll to zoom"
      camera-orbit={orbit}
    />
  );
}

function meshStats(r: Asset["recon"]): string[] {
  const out: string[] = [];
  if (r.tris) out.push(r.tris >= 1e6 ? `${(r.tris / 1e6).toFixed(2)}M tris` : `${r.tris.toLocaleString()} tris`);
  if (r.size_mb) out.push(`${r.size_mb} MB`);
  if (r.texture_px) out.push(`${r.texture_px}px texture`);
  return out;
}

function ReconCard({ job, a, i, onOpen }: { job: Job; a: Asset; i: number; onOpen: () => void }) {
  const r = a.recon;
  const crop = fileUrl(job, a.crop);
  const done = r.status === "ready" || r.status === "proxy";
  const mesh = done && r.mesh ? `${fileUrl(job, r.mesh)}?v=${r.tris ?? 0}` : undefined;
  return (
    <div className={`card ${r.status}`} style={{ animationDelay: `${i * 30}ms` }}>
      <div className="viewer">
        {/* Raw meshes are ~1.4M tris: auto-rotating several at once saturates the GPU
            and the page stops taking drags. They stay still until you drag one. */}
        {mesh ? (
          <MeshView src={mesh} auto={(r.tris ?? 0) < 200_000} />
        ) : (
          <span className="label dim">{r.status === "working" ? "Generating…" : "3D appears here"}</span>
        )}
        {crop && (
          <div className="photo-inset" title="Source photo from the walkthrough">
            <img src={crop} alt={`${a.id} source photo`} />
          </div>
        )}
        {r.status === "proxy" && <span className="tag orange viewer-tag">Proxy</span>}
        {r.status === "ready" && <span className="tag green viewer-tag">Raw · as received</span>}
        {mesh && <span className="viewer-hint">Drag to orbit · scroll to zoom</span>}
      </div>
      <div className="meta">
        <div className="row">
          <span className="label">{a.id}</span>
          <span className="spacer" />
          {done && <button className="tag" onClick={onOpen}>Inspect ↗</button>}
        </div>
        {done && meshStats(r).length > 0 && <span className="label dim">{meshStats(r).join(" · ")}</span>}
        <StepList steps={r.steps} />
        {r.status === "proxy" && r.chip && <div className="chip">{r.chip}</div>}
      </div>
    </div>
  );
}

function Inspector({ job, a, onClose }: { job: Job; a: Asset; onClose: () => void }) {
  const r = a.recon;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  const rows: [string, string][] = [
    ["Source", r.source === "proxy" ? "Fitted proxy cuboid" : `Tripo image-to-3D · v3.1 · standard texture${r.tris && r.tris > 600_000 ? " · uncapped" : " · capped at 500k tris"}`],
    ["Triangles", r.tris ? r.tris.toLocaleString() : "—"],
    ["File size", r.size_mb ? `${r.size_mb} MB` : "—"],
    ["Texture", r.texture_px ? `${r.texture_px} × ${r.texture_px} base colour` : "None"],
    ["Generation time", r.seconds ? `${r.seconds}s` : "—"],
    ["Credits", r.credits === 0 ? "0 (cached)" : r.credits ? `${r.credits}` : "—"],
    ["Seen in video", `${a.seen_in} frames · detector confidence ${a.conf?.toFixed(2) ?? "—"}`],
  ];
  return (
    <div className="inspector-bg" onClick={onClose}>
      <div className="inspector" onClick={(e) => e.stopPropagation()}>
        <div className="panel-hd">
          <span className="label"><span className="n">{a.id}</span> / Mesh inspector · drag to orbit · scroll to zoom</span>
          <button className="tag" onClick={onClose}>Close · Esc</button>
        </div>
        <div className="inspector-body">
          <div className="inspector-view">{r.mesh && <MeshView src={`${fileUrl(job, r.mesh)}?v=${r.tris ?? 0}`} auto={false} />}</div>
          <aside className="inspector-side">
            <div className="inspector-photo">{a.crop && <img src={fileUrl(job, a.crop)} alt="Source crop" />}</div>
            <span className="label dim">Source photo · cut from the walkthrough</span>
            <dl className="facts">
              {rows.map(([k, v]) => (
                <div key={k}><dt className="label dim">{k}</dt><dd>{v}</dd></div>
              ))}
            </dl>
            <StepList steps={r.steps} />
            <div className="note">
              This is the mesh exactly as Tripo returned it. Nothing has been cleaned yet. SimReady decimates it,
              orients it, scales it to real size and adds physics, each as a visible step.
            </div>
          </aside>
        </div>
      </div>
    </div>
  );
}

export function ReconstructPanel({ job, busy, onGenerate }: { job: Job | null; busy: boolean; onGenerate: () => void }) {
  const st = job?.stages.reconstruct;
  const assets = job?.assets.filter((a) => !a.excluded && a.crop) ?? [];
  const [plan, setPlan] = useState<Plan | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const inspected = assets.find((a) => a.id === open);
  const segDone = job?.stages.segment.status === "done";
  const keptIds = assets.map((a) => a.id).join(",");

  // Refresh cost + balance now and every 10 s, so a top-up shows up without a
  // reload and a fetch that failed (e.g. server restarting) retries itself.
  useEffect(() => {
    if (!segDone || busy) return;
    let live = true;
    const load = () =>
      fetch("/api/reconstruct/plan")
        .then((r) => (r.ok ? r.json() : Promise.reject()))
        .then((p) => live && setPlan(p))
        .catch(() => {});
    load();
    const timer = window.setInterval(load, 10_000);
    return () => { live = false; window.clearInterval(timer); };
  }, [segDone, busy, keptIds, st?.status]);

  const short = plan && plan.balance !== null && plan.balance < plan.credits;
  const affordable = plan && plan.balance !== null ? Math.floor(plan.balance / plan.credits_per_model) : 0;
  const broke = !!plan && plan.to_generate > 0 && plan.balance !== null && affordable === 0;
  const cost = plan ? `${plan.credits} credits · $${(plan.credits / 100).toFixed(2)}` : "…";

  return (
    <Panel id="p-reconstruct" n={3} title="Reconstruct" state={st} className="span-3">
      {segDone && (
        <div className="cost">
          <div className="cost-item"><span className="label dim">Boxes</span><b>{assets.length}</b></div>
          <div className="cost-item"><span className="label dim">Cached</span><b>{plan?.cached ?? "…"}</b></div>
          <div className="cost-item">
            <span className="label dim">This run</span>
            <b>{cost}</b>
          </div>
          <div className={`cost-item ${short ? "warn" : ""}`}>
            <span className="label dim">Tripo balance</span>
            <b>{plan?.balance === null ? "—" : plan ? `${plan.balance.toFixed(0)} credits` : "…"}</b>
          </div>
          <span className="spacer" />
          {broke ? (
            <>
              <button className="btn btn-ghost" disabled={busy} onClick={onGenerate}>Use proxy boxes instead</button>
              <button className="btn btn-primary" disabled>Top up Tripo credits</button>
            </>
          ) : (
            <button className="btn btn-primary" disabled={busy || !assets.length || !plan} onClick={onGenerate}>
              {!plan
                ? "Checking balance…"
                : plan.to_generate === 0
                  ? "Rebuild from cache"
                  : `Generate ${short ? affordable : plan.to_generate} meshes`}
            </button>
          )}
        </div>
      )}
      {plan?.chip && <div className="chip">{plan.chip}</div>}
      {broke && !plan?.chip && (
        <div className="chip">
          Your Tripo API balance is 0. API credits are separate from a Tripo Studio plan: add them under
          Billing at developers.tripo3d.ai. This panel updates on its own once they arrive.
        </div>
      )}
      {short && !broke && !plan?.chip && (
        <div className="chip">
          Enough credits for {affordable} of {plan!.to_generate} boxes. The clearest boxes get meshes; the rest
          become proxy boxes.
        </div>
      )}

      {assets.length && st && st.status !== "idle" ? (
        <div className="cards">
          {assets.map((a, i) => <ReconCard key={a.id} job={job!} a={a} i={i} onOpen={() => setOpen(a.id)} />)}
        </div>
      ) : (
        <div className="empty-note">
          {segDone
            ? "Textured meshes, 30 credits per box. Proxy boxes get a plain cardboard material in SimReady."
            : "Run Segment first. One card per box; bad generations swap to a proxy automatically."}
        </div>
      )}
      {inspected && job && <Inspector job={job} a={inspected} onClose={() => setOpen(null)} />}
    </Panel>
  );
}

/* ---------------- 04 SIMREADY ---------------- */
function TapeInputs({ a, disabled }: { a: Asset; disabled: boolean }) {
  const init = a.measured ? [a.measured.l, a.measured.w, a.measured.h].map(String) : ["", "", ""];
  const [vals, setVals] = useState<string[]>(init);
  const [err, setErr] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const key = a.measured ? `${a.measured.l}|${a.measured.w}|${a.measured.h}` : "";
  const [seen, setSeen] = useState(key);
  if (seen !== key) {
    // Server state changed (another tab, a reload): show it.
    setSeen(key);
    setVals(init);
  }

  const save = async (next: string[]) => {
    const nums = next.map((v) => (v.trim() === "" ? null : Number(v)));
    if (nums.some((n) => n !== null && Number.isNaN(n))) return setErr("Numbers only");
    if (nums.some((n) => n === null) && nums.some((n) => n !== null)) return; // wait for all three
    const [l, w, h] = nums;
    const r = await fetch(`/api/assets/${a.id}/measured`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ l, w, h }),
    });
    if (!r.ok) setErr((await r.json().catch(() => ({}))).detail ?? "Could not save");
    else { setErr(null); setSaved(true); window.setTimeout(() => setSaved(false), 1200); }
  };

  return (
    <div className="tape">
      <span className="label dim" title="Optional. Leave empty and the size is a best guess from the box's proportions.">
        Tape cm
      </span>
      <div className="tape-row">
        {["L", "W", "H"].map((p, i) => (
          <input
            key={p}
            className="input"
            inputMode="decimal"
            placeholder={p}
            value={vals[i]}
            disabled={disabled}
            onChange={(e) => { const n = vals.slice(); n[i] = e.target.value; setVals(n); }}
            onBlur={() => save(vals)}
            onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
          />
        ))}
        <span className={`label ${saved ? "" : "dim"}`} style={{ color: saved ? "var(--green)" : undefined }}>
          {saved ? "Saved" : a.measured ? "Measured" : "Best guess"}
        </span>
      </div>
      {err && <div className="chip">{err}</div>}
    </div>
  );
}

function SimReadyCard({ job, a, busy, onOpen }: { job: Job; a: Asset; busy: boolean; onOpen: () => void }) {
  const [view, setView] = useState<"clean" | "raw">("clean");
  const [showChecks, setShowChecks] = useState(false);
  const allPass = a.checks.length > 0 && a.checks.every((c) => c.status === "pass");
  const raw = a.recon.mesh ? `${fileUrl(job, a.recon.mesh)}?v=${a.recon.tris ?? 0}` : undefined;
  const clean = a.clean_mesh ? `${fileUrl(job, a.clean_mesh)}?v=${a.clean_tris ?? 0}-${a.dims?.l ?? 0}` : undefined;
  const shown = view === "clean" ? clean : raw;
  const flag = a.checks.some((c) => c.status === "warn" || c.status === "fail") || a.prep.some((s) => s.status === "failed");
  const running = a.prep.some((s) => s.status === "working");
  return (
    <div className={`check-card sr-card ${flag ? "flag" : ""} ${running ? "running" : ""}`}>
      <div className="row">
        <span className="label">{a.id}</span>
        {a.recon.status === "proxy" && <span className="tag orange">Proxy</span>}
        <span className="spacer" />
        <span className="label dim">{a.dims ? `${cm(a.dims)} cm` : ""}</span>
        {allPass && (
          <button className="tag" onClick={() => setShowChecks((v) => !v)} title="The 9 validation checks run on the written USD">
            {showChecks ? "Hide checks" : "Checks"}
          </button>
        )}
      </div>

      <div className="sr-view">
        {shown ? <MeshView src={shown} auto={view === "clean"} /> :<span className="label dim">{view === "clean" ? "Not cleaned yet" : "No mesh"}</span>}
        <div className="seg-toggle">
          <button className={view === "raw" ? "on" : ""} onClick={() => setView("raw")}>
            Raw{a.recon.tris ? ` · ${a.recon.tris >= 1e6 ? (a.recon.tris / 1e6).toFixed(2) + "M" : a.recon.tris}` : ""}
          </button>
          <button className={view === "clean" ? "on" : ""} onClick={() => setView("clean")} disabled={!clean}>
            Clean{a.clean_tris ? ` · ${(a.clean_tris / 1000).toFixed(0)}k` : ""}
          </button>
        </div>
        {clean && <button className="tag sr-inspect" onClick={onOpen}>Compare ↗</button>}
      </div>

      <TapeInputs a={a} disabled={busy} />

      {a.prep.length > 0 && <StepList steps={a.prep} />}

      {a.checks.length > 0 && (allPass && !showChecks ? null : (
        <div className="sr-checks">
          {a.checks.map((c) => (
            <div key={c.rule} className={`check-row ${c.status}`} title={c.detail}>
              <span className={`dot ${c.status}`} />
              {c.label}
              <span className="d">{c.status === "pass" ? "" : c.detail || c.status}</span>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

function CompareInspector({ job, a, onClose }: { job: Job; a: Asset; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="inspector-bg" onClick={onClose}>
      <div className="inspector" onClick={(e) => e.stopPropagation()}>
        <div className="panel-hd">
          <span className="label"><span className="n">{a.id}</span> / Raw vs SimReady · drag to orbit</span>
          <button className="tag" onClick={onClose}>Close · Esc</button>
        </div>
        <div className="compare">
          <div className="compare-col">
            <span className="label dim">Raw from Tripo · {a.recon.tris?.toLocaleString()} tris · {a.recon.size_mb} MB · Y-up · unitless</span>
            <div className="inspector-view">{a.recon.mesh && <MeshView src={fileUrl(job, a.recon.mesh)!} auto={false} />}</div>
          </div>
          <div className="compare-col">
            <span className="label dim">
              SimReady · {a.clean_tris?.toLocaleString()} tris · {a.dims ? `${cm(a.dims)} cm` : ""} · Z-up · meters
            </span>
            <div className="inspector-view">{a.clean_mesh && <MeshView src={fileUrl(job, a.clean_mesh)!} auto={false} />}</div>
          </div>
        </div>
        <div className="inspector-steps"><StepList steps={a.prep} full /></div>
        <span className="label dim">USD: jobs/{job.id}/{a.usd}</span>
      </div>
    </div>
  );
}

export function SimReadyPanel({ job, busy, onRun }: { job: Job | null; busy: boolean; onRun: () => void }) {
  const st = job?.stages.simready;
  const assets = job?.assets.filter((a) => !a.excluded && a.recon.mesh) ?? [];
  const reconDone = job && ["done", "attention"].includes(job.stages.reconstruct.status);
  const [open, setOpen] = useState<string | null>(null);
  const inspected = assets.find((a) => a.id === open);
  const measured = assets.filter((a) => a.measured).length;
  const written = assets.filter((a) => a.usd).length;

  return (
    <Panel id="p-simready" n={4} title="SimReady" state={st} className="span-3">
      {reconDone && (
        <div className="cost">
          <div className="cost-item"><span className="label dim">Assets</span><b>{assets.length}</b></div>
          <div className="cost-item">
            <span className="label dim">Sized by</span>
            <b>{measured ? `${measured} tape · ${assets.length - measured} guess` : "Best guess"}</b>
          </div>
          <div className="cost-item"><span className="label dim">USD written</span><b>{written}</b></div>
          <span className="cost-note">
            Sizes are matched to the closest common carton by proportions. Type a tape size to override; it also
            anchors the others.
          </span>
          <span className="spacer" />
          <button className="btn btn-primary" disabled={busy || !assets.length} onClick={onRun}>
            {written ? "Re-run SimReady" : `Run SimReady on ${assets.length} assets`}
          </button>
        </div>
      )}

      {assets.length && job ? (
        <div className="checks sr-grid">
          {assets.map((a) => <SimReadyCard key={a.id} job={job} a={a} busy={busy} onOpen={() => setOpen(a.id)} />)}
        </div>
      ) : (
        <div className="empty-note">Run Reconstruct first. Each mesh is repaired, decimated, oriented, scaled, given physics, written to USD and validated, step by step.</div>
      )}
      {inspected && job && <CompareInspector job={job} a={inspected} onClose={() => setOpen(null)} />}
    </Panel>
  );
}

/* ---------------- 05 STAGE ---------------- */
export function StagePanel({ job, busy, onAssemble, onSettle }: { job: Job | null; busy: boolean; onAssemble: () => void; onSettle: () => void }) {
  const st = job?.stages.stage;
  const info = job?.stage;
  const ready = !!info?.usd && st?.status !== "working";
  const simDone = job && ["done", "attention"].includes(job.stages.simready.status);
  const preview = job && info?.preview ? `${fileUrl(job, info.preview)}?v=${info.layout.length}-${info.floor_m?.[0] ?? 0}` : undefined;
  const [isaac, setIsaac] = useState<{ state: string; version: string | null; scene?: { state: string; detail: string } | null } | null>(null);
  const [launchErr, setLaunchErr] = useState<string | null>(null);
  const [showChecks, setShowChecks] = useState(false);
  const allPass = !!info?.checks.length && info.checks.every((c) => c.status === "pass");

  useEffect(() => {
    let live = true;
    const load = () =>
      fetch("/api/isaac").then((r) => r.json()).then((s) => live && setIsaac(s)).catch(() => {});
    load();
    const t = window.setInterval(load, 4000);
    return () => { live = false; window.clearInterval(t); };
  }, []);

  const launch = async (play: boolean) => {
    setLaunchErr(null);
    const r = await fetch(`/api/isaac/launch?play=${play}`, { method: "POST" });
    if (!r.ok) setLaunchErr((await r.json().catch(() => ({}))).detail ?? "Could not launch Isaac Sim");
    else setIsaac((s) => (s ? { ...s, state: "running" } : s));
  };

  const isaacLabel: Record<string, string> = {
    ready: `Isaac Sim ${isaac?.version ?? ""} · ready`,
    running: !isaac?.scene
      ? "Isaac Sim is starting… (first launch can take ~2 min)"
      : isaac.scene.state === "opened" ? `Isaac Sim · your stage is open · ${isaac.scene.detail}`
      : isaac.scene.state === "playing" ? `Isaac Sim · playing · ${isaac.scene.detail}`
      : isaac.scene.state === "failed" ? "Isaac Sim couldn't open the stage"
      : "Isaac Sim · opening your stage…",
    installing: "Isaac Sim · installing…",
    missing: "Isaac Sim · not installed",
  };

  return (
    <Panel id="p-stage" n={5} title="Stage" state={st}>
      <div className="stage-view">
        {preview ? (
          <MeshView src={preview} auto={false} orbit="-25deg 62deg 70%" />
        ) : (
          <div className="frame-empty label dim">{simDone ? "Press Assemble to build the scene" : "Run SimReady first"}</div>
        )}
        {preview && <span className="viewer-hint">Drag to orbit · scroll to zoom</span>}
      </div>

      <div className="stage-side">
        <div className="dim-tiles">
          <div className="dim-tile"><span className="label dim">Boxes</span><b>{info?.layout.length || "—"}</b></div>
          <div className="dim-tile">
            <span className="label dim">Floor</span>
            <b>{info?.floor_m ? `${info.floor_m[0].toFixed(1)}×${info.floor_m[1].toFixed(1)}` : "—"}<small>m</small></b>
          </div>
          <div className="dim-tile"><span className="label dim">Mass</span><b>{info?.total_mass_kg ?? "—"}<small>kg</small></b></div>
        </div>

        <button className="btn btn-primary btn-wide" disabled={busy || !simDone} onClick={onAssemble}>
          {info?.usd ? "Re-assemble stage" : "Assemble stage"}
        </button>

        {info && info.steps.length > 0 && <StepList steps={info.steps} />}

        {info && info.checks.length > 0 && (
          <div className="stage-checks">
            <div className="row">
              <span className={`st ${allPass ? "pass" : "warn"}`}>
                <span className={`dot ${allPass ? "pass" : "warn"}`} />
                {info.checks.filter((c) => c.status === "pass").length} / {info.checks.length} stage checks pass
              </span>
              <span className="spacer" />
              <button className="tag" onClick={() => setShowChecks((v) => !v)}>{showChecks ? "Hide" : "Checks"}</button>
            </div>
            {(showChecks || !allPass) && info.checks.map((c) => (
              <div key={c.rule} className={`check-row ${c.status}`} title={c.detail}>
                <span className={`dot ${c.status}`} />
                {c.label}
                <span className="d">{c.detail}</span>
              </div>
            ))}
          </div>
        )}

        <div className="isaac">
          <StatusDot
            s={isaac?.state === "ready" || isaac?.state === "running" ? "done" : isaac?.state === "installing" ? "working" : "idle"}
            text={isaac ? isaacLabel[isaac.state] : "Checking Isaac Sim…"}
          />
          <div className="isaac-buttons">
            <button className="btn btn-ghost" disabled={busy || !ready || isaac?.state === "missing" || isaac?.state === "installing"}
              onClick={onSettle} title="Drop the boxes in real Isaac Sim physics without opening a window, and report where they land">
              Test physics
            </button>
            <button className="btn btn-outline" disabled={!ready || isaac?.state !== "ready"} onClick={() => launch(false)}>
              Launch Isaac
            </button>
            <button className="btn btn-ghost" disabled={!ready || isaac?.state !== "ready"} onClick={() => launch(true)}
              title="Open the stage and press Play: boxes drop and settle">
              Launch + play
            </button>
          </div>
          {launchErr && <div className="chip">{launchErr}</div>}
          {isaac?.scene?.state === "failed" && <div className="chip">{isaac.scene.detail}</div>}
        </div>
      </div>
    </Panel>
  );
}
