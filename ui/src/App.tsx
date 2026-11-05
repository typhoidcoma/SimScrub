import { useState } from "react";
import { Footer, Header, ProcessMonitor, ProjectName, Tabs } from "./components";
import { type Source, CapturePanel, ReconstructPanel, SegmentPanel, SimReadyPanel, StagePanel } from "./panels";
import { TabVisible } from "./tabVisible";
import { STAGES, type StageName } from "./types";
import { api, useJob } from "./useJob";

const TITLES: Record<StageName, string> = {
  capture: "Capture", segment: "Segment", reconstruct: "Reconstruct", simready: "SimReady", stage: "Stage",
};

export default function App() {
  const { job, online } = useJob();
  const [showLog, setShowLog] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // A tab the artist clicked, remembered with whichever stage was running at the
  // time. When a different stage starts, the view follows the pipeline again.
  const [picked, setPicked] = useState<{ tab: StageName; during: StageName | undefined } | null>(null);

  const busy = job ? STAGES.some((s) => job.stages[s].status === "working") : false;
  const working = job ? STAGES.find((s) => job.stages[s].status === "working") : undefined;
  const active: StageName = picked && picked.during === working ? picked.tab : working ?? picked?.tab ?? "capture";

  const idx = STAGES.indexOf(active);
  const next = STAGES[idx + 1];
  const activeDone = job && ["done", "attention"].includes(job.stages[active].status);

  const pick = (tab: StageName) => setPicked({ tab, during: working });

  const run = (path: string) => {
    setError(null);
    api(path).catch((e: Error) => setError(e.message));
  };

  const process = async (src: Source) => {
    if (!src) return;
    setError(null);
    try {
      let r: Response;
      if ("file" in src) {
        const fd = new FormData();
        fd.append("video", src.file);
        r = await fetch("/api/process", { method: "POST", body: fd });
      } else {
        r = await fetch(`/api/process?path=${encodeURIComponent(src.path)}`, { method: "POST" });
      }
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? "Could not start processing.");
      setPicked(null);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const kept = job?.assets.filter((a) => !a.excluded).length ?? 0;
  const proxies = job?.assets.filter((a) => !a.excluded && a.recon.status === "proxy").length ?? 0;
  const approved = job ? STAGES.filter((s) => job.stages[s].status === "done").length : 0;

  return (
    <div className="shell">
      <div className="frame">
        <Header online={online} job={job} />
        <Tabs job={job} active={active} onPick={(s) => pick(s as StageName)} />
        <main className="body">
          <div className="hero">
            <div>
              <span className="label">
                <span className="n">0{idx + 1}</span> / {TITLES[active]}
              </span>
              <ProjectName name={job?.name ?? "Untitled project"} />
              <div className="sub">
                {kept} boxes · {proxies} proxy · {approved}/5 stages approved
              </div>
            </div>
            <div className="hero-actions">
              {next && activeDone && !busy && (
                <button className="btn btn-ghost" onClick={() => pick(next)}>
                  Next · {TITLES[next]} →
                </button>
              )}
            </div>
          </div>
          {error && <div className="chip">{error}</div>}

          <div className="tab-body">
            <div hidden={active !== "capture"}><TabVisible.Provider value={active === "capture"}><CapturePanel job={job} busy={busy} onProcess={process} /></TabVisible.Provider></div>
            <div hidden={active !== "segment"}><TabVisible.Provider value={active === "segment"}>
              <SegmentPanel job={job} onToggle={(id) => run(`/api/assets/${id}/exclude`)} />
            </TabVisible.Provider></div>
            <div hidden={active !== "reconstruct"}><TabVisible.Provider value={active === "reconstruct"}>
              <ReconstructPanel job={job} busy={busy} onGenerate={() => { setPicked(null); run("/api/reconstruct"); }} />
            </TabVisible.Provider></div>
            <div hidden={active !== "simready"}><TabVisible.Provider value={active === "simready"}>
              <SimReadyPanel job={job} busy={busy} onRun={() => { setPicked(null); run("/api/simready"); }} />
            </TabVisible.Provider></div>
            <div hidden={active !== "stage"}><TabVisible.Provider value={active === "stage"}><StagePanel job={job} /></TabVisible.Provider></div>
          </div>

          <ProcessMonitor job={job} showLog={showLog} onToggleLog={() => setShowLog((v) => !v)} />
        </main>
        <Footer job={job} />
      </div>
    </div>
  );
}
