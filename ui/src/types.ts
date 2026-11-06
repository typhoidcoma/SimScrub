// Mirrors simscrub/manifest.py. The manifest is the only state the UI renders.

export type Status = "idle" | "working" | "done" | "attention" | "failed";
export type StageName = "capture" | "segment" | "reconstruct" | "simready" | "stage";
export const STAGES: StageName[] = ["capture", "segment", "reconstruct", "simready", "stage"];

export interface StageState { status: Status; progress: number; message: string }
export interface Dims { l: number; w: number; h: number }

export interface Step {
  name: string;
  status: "waiting" | "working" | "done" | "skipped" | "failed";
  detail: string;
  more?: string;
}

export interface Recon {
  source: "tripo" | "triposr" | "proxy" | null;
  status: "queued" | "working" | "ready" | "proxy" | "failed";
  chip: string | null;
  mesh: string | null;
  tris: number | null;
  size_mb: number | null;
  texture_px: number | null;
  seconds: number | null;
  credits: number | null;
  task_id: string | null;
  steps: Step[];
}

export interface Check {
  rule: string;
  label: string;
  status: "pass" | "fixed" | "warn" | "fail";
  detail: string;
}

export interface Asset {
  id: string;
  label: string;
  bbox: [number, number, number, number] | null;
  polygon: [number, number][] | null;
  track: number | null;
  conf: number | null;
  best_frame: string | null;
  crop: string | null;
  seen_in: number;
  excluded: boolean;
  flag: string | null;
  measured: Dims | null;
  dims: Dims | null;
  dims_source: "tape" | "inferred" | "estimated" | null;
  thumb: string | null;
  recon: Recon;
  prep: Step[];
  checks: Check[];
  usd: string | null;
  clean_mesh: string | null;
  clean_tris: number | null;
}

export interface Job {
  id: string;
  name: string;
  stages: Record<StageName, StageState>;
  capture: {
    video: string | null;
    frame: string | null;
    frames_extracted: number;
    duration: number | null;
    resolution: [number, number] | null;
  };
  assets: Asset[];
  stage: {
    usd: string | null;
    preview: string | null;
    floor_m: number[] | null;
    total_mass_kg: number | null;
    layout: { id: string; x: number; y: number; z: number; on: string | null; source: string }[];
    steps: Step[];
    checks: Check[];
  };
  log: { t: number; stage: string; text: string }[];
}

/** Job files are stored job-relative; served from the job folder. */
export const fileUrl = (job: Job, rel: string | null) =>
  !rel ? undefined : rel.startsWith("/") ? rel : `/api/jobs/${job.id}/files/${rel}`;
