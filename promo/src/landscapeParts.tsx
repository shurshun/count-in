import React from "react";
import { AbsoluteFill, Easing, Img, interpolate, staticFile } from "remotion";
import { C, EndCard, FONT, ONE, Soundtrack, T, clamp, f } from "./shared";
import { Intro } from "./intro";
import { FOCUS } from "./zoom";
import { Flash, Footer, Header, useLine } from "./verticalParts";

// Parts of the 16:9 site video: the frame (intro, scene, end card), the focus point, the ring, the lens image.

/** The current line plus the focus point gliding between the controls; `show` is 1 while a control is in focus. */
export const useFocus = () => {
  const l = useLine();
  const { frame, at, i } = l;
  const p = interpolate(frame, [f(at[i]), f(at[i]) + 14], [0, 1], { ...clamp, easing: Easing.bezier(0.2, 0.8, 0.2, 1) });
  const prev = FOCUS[Math.max(0, i - 1)], cur = FOCUS[i];
  const mix = (k: number) => prev[k] + (cur[k] - prev[k]) * p;
  const [l0, r0] = l.line.text.split(l.line.accent!).map((t) => t.trim());
  return { ...l, fx: mix(0), fy: mix(1), z: mix(2), show: (prev[2] > 1 ? 1 : 0) * (1 - p) + (cur[2] > 1 ? 1 : 0) * p,
    before: l0, accent: l.line.accent!, after: r0 };
};

/** Font size that keeps `text` within `width` px, capped at `max`. */
export const fit = (text: string, width: number, max: number) => Math.min(max, width / (0.6 * text.length));

/** Intro, then the scene on orange, then the end card. */
export const Frame = ({ children }: { children: React.ReactNode }) => {
  const { frame, copy } = useLine();
  return (
    <AbsoluteFill style={{ background: C.accent, fontFamily: FONT }}>
      <Soundtrack />
      {frame < f(ONE) ? <Intro syllables={copy.syllables} />
        : frame < f(T.end) ? children
        : <EndCard tagline={copy.tagline} bg={C.accent} fg="rgba(0,0,0,.6)" />}
    </AbsoluteFill>
  );
};

export const Shot = ({ src, w }: { src: string; w: number }) => (
  <Img src={staticFile(src)} style={{ width: w, display: "block", borderRadius: 18, boxShadow: "0 30px 80px rgba(120,60,0,.4)" }} />
);

/** Ring on the control, centred on (x, y) of its parent. */
export const Ring = ({ x, y, d = 92, pulse, opacity }: { x: number; y: number; d?: number; pulse: number; opacity: number }) => (
  <div style={{ position: "absolute", left: x - d / 2, top: y - d / 2, width: d, height: d, borderRadius: d, opacity,
    border: `${d * 0.09}px solid ${C.accent}`, boxShadow: "0 0 0 5px #fff, 0 8px 24px rgba(0,0,0,.35)", scale: `${1 + 0.18 * pulse}` }} />
);

/** The screenshot at scale `s`, placed so the focus point sits in the middle of a w×h box. */
export const Magnified = ({ src, size, w, h, fx, fy, s }: { src: string; size: [number, number]; w: number; h: number; fx: number; fy: number; s: number }) => {
  const [W, H] = size;
  const x = Math.min(0, Math.max(w - W * s, w / 2 - fx * W * s));
  const y = Math.min(0, Math.max(h - H * s, h / 2 - fy * H * s));
  return <Img src={staticFile(src)} style={{ position: "absolute", left: 0, top: 0, width: W, height: H, maxWidth: "none", transformOrigin: "0 0",
    transform: `translate(${x}px, ${y}px) scale(${s})` }} />;
};

/** Full-frame white flash when the guitar and the vocals drop out. */
export const Drop = ({ opacity = 0.5 }: { opacity?: number }) => <AbsoluteFill style={{ opacity }}><Flash /></AbsoluteFill>;

export const Brand = ({ left, top }: { left: number; top: number }) => <Header style={{ left, top, right: "auto" }} />;

/** Platforms, price and address in an 800 px block, scaled from its top left corner. */
export const Foot = ({ left, top, scale = 1 }: { left: number; top: number; scale?: number }) => (
  <div style={{ position: "absolute", left, top, width: 800, height: 220, transformOrigin: "0 0", transform: `scale(${scale})` }}>
    <Footer top={0} />
  </div>
);
