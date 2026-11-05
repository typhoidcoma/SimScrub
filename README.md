# TE SimScrub

Video in, SimReady USD out. A walkthrough of a real office with cardboard boxes becomes a labeled, physics-ready, true-scale USD stage for Isaac Sim, driven from one artist-friendly page.

## Run (dev)

```bash
# backend
python -m venv .venv
.venv/Scripts/python -m pip install -e .[dev]
.venv/Scripts/python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
.venv/Scripts/python -m pip install ultralytics opencv-python
.venv/Scripts/python -m uvicorn server.app:app --port 8000

# frontend (second terminal)
cd ui && npm install && npm run dev
```

Open http://localhost:5173, drop a walkthrough video into **01 Capture** (or pick one sitting in the project folder) and press **Process**.

Headless:

```bash
.venv/Scripts/python -m simscrub.cli segment path/to/walkthrough.mp4 --job office
```

Writes `jobs/office/` with frames, one transparent crop per box, `manifest.json`, and `segment_overlay.jpg` for a quick visual check.

## How box finding works

1. **Capture** samples the video at 8 fps, scales frames to 1600 px, and drops frames much blurrier than the median.
2. **Segment** runs YOLOE-26L-seg with the text prompts `cardboard box` / `brown cardboard box`, so it only looks for boxes. Weights download from the Ultralytics GitHub releases on first run into `models/`.
3. Detections are linked into one track per physical box with an IoU tracker plus **camera-motion compensation** (optical flow on the background), so a box that pans out of view and back keeps its identity.
4. Each box's clearest view (large, sharp, not cut off at the frame edge) is cropped from the **full-resolution** source frame with a transparent background. These crops feed Tripo.
5. Weak tracks (low confidence and only a few frames) are pre-excluded with a reason. The artist can tap any box in **02 Segment** to keep or exclude it.

## Layout

| Path | What |
|---|---|
| `simscrub/manifest.py` | Job manifest, the single source of truth every stage reads and writes |
| `simscrub/stages/capture.py` | Frame extraction and sharpness scoring |
| `simscrub/stages/segment.py` | YOLOE box finding, tracking, crops |
| `simscrub/pipeline.py` | Stage runners shared by the CLI and the server |
| `simscrub/errors.py` | Exceptions mapped to plain-language chips for the UI |
| `simscrub/cli.py` | Command line entry point |
| `server/app.py` | FastAPI server; pushes the manifest over `/ws` after every change |
| `ui/` | React + Vite front end, one tab per stage |

## Status

- [x] UI shell, tabbed stages, live process monitor
- [x] Capture + Segment on real video
- [ ] Reconstruct (Tripo API, proxy fallback)
- [ ] SimReady cleanup + USD validation
- [ ] Stage assembly + Isaac Sim launch
