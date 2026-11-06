import type { DetailedHTMLProps, HTMLAttributes } from "react";

// <model-viewer> web component (spinnable GLB preview on each RECONSTRUCT card).
type ModelViewerProps = DetailedHTMLProps<HTMLAttributes<HTMLElement>, HTMLElement> & {
  src?: string;
  "camera-controls"?: boolean;
  "auto-rotate"?: boolean;
  "auto-rotate-delay"?: string;
  "rotation-per-second"?: string;
  "interaction-prompt"?: string;
  "shadow-intensity"?: string;
  exposure?: string;
  "camera-orbit"?: string;
};

declare module "react" {
  namespace JSX {
    interface IntrinsicElements {
      "model-viewer": ModelViewerProps;
    }
  }
}
