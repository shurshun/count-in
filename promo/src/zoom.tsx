import { Easing, Img, interpolate, staticFile, useCurrentFrame } from "remotion";
import { T, clamp, f } from "./shared";

// Focus point (fraction of the screenshot) and zoom for each line, in line order
export const FOCUS: [number, number, number][] = [
  [0.5, 0.5, 1],      // whole window
  [0.17, 0.8, 2.6],   // tempo card
  [0.45, 0.2, 3],     // the orange "1" marker
  [0.25, 0.25, 2.4],  // the clicks
  [0.49, 0.82, 2.6],  // count-in card
  [0.81, 0.06, 3.4],  // Preview button
  [0.82, 0.78, 2.8],  // backing track, guitar muted
  [0.6, 0.45, 1.6],   // waveform
  [0.82, 0.8, 2.8],   // backing track, vocals
  [0.5, 0.5, 1],      // whole window
];

/** The screenshot fills a w×h box and flies between focus points as the lines change. */
export const ZoomShot = ({ w, h, src, size, starts }: { w: number; h: number; src: string; size: [number, number]; starts: number[] }) => {
  const frame = useCurrentFrame();
  const [W, H] = size;
  const fit = Math.min(w / W, h / H) * 0.92;
  let i = 0;
  while (i + 1 < starts.length && frame >= f(starts[i + 1])) i++;
  const prev = FOCUS[Math.max(0, i - 1)];
  const cur = FOCUS[i];
  const p = interpolate(frame, [f(starts[i]), f(starts[i]) + 14], [0, 1], { ...clamp, easing: Easing.bezier(0.2, 0.8, 0.2, 1) });
  const mix = (k: number) => prev[k] + (cur[k] - prev[k]) * p;
  const end = interpolate(frame, [f(T.end - 1), f(T.end)], [0, 1], clamp);
  const [fx, fy, z] = [mix(0), mix(1), mix(2) * (1 - end) + end];
  const s = fit * z;

  // Keep the zoomed image covering the box, so no edge shows
  const cx = Math.min(Math.max(fx * W * s, w / 2), W * s - w / 2);
  const cy = Math.min(Math.max(fy * H * s, h / 2), H * s - h / 2);
  const x = W * s <= w ? (w - W * s) / 2 : w / 2 - cx;
  const y = H * s <= h ? (h - H * s) / 2 : h / 2 - cy;
  return (
    <div style={{ position: "absolute", inset: 0, overflow: "hidden" }}>
      <Img src={staticFile(src)} style={{ position: "absolute", left: 0, top: 0, width: W, height: H, transformOrigin: "0 0",
        transform: `translate(${x}px, ${y}px) scale(${s})`, borderRadius: 24 / s, boxShadow: "0 30px 90px rgba(0,0,0,.25)" }} />
    </div>
  );
};
