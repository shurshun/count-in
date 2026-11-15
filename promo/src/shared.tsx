import React from "react";
import { AbsoluteFill, Audio, Img, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig } from "remotion";

export const FPS = 30;
export const LEN = 30 * FPS;

// The demo clip: 121.06 BPM, two bars of build-up clicks, the song's "one" after eight beats
export const BEAT = 60 / 121.06;
export const BAR = 4 * BEAT;
export const ONE = 8 * BEAT;
export const QUARTERS = [0, 1, 2, 3].map((i) => i * BEAT);
export const EIGHTHS = [0, 1, 2, 3, 4, 5, 6, 7].map((i) => 4 * BEAT + (i * BEAT) / 2);

// Seconds where the guitar goes, the vocals go and the end card starts
export const T = { guitar: 17, vocals: 21, end: 24.5 };

export const f = (s: number) => Math.round(s * FPS);
export const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;

export const C = {
  bg: "#fbfbfd", bg2: "#f2f2f5", card: "#fff", border: "#e3e3e8", text: "#1d1d1f", muted: "#6e6e73", accent: "#ff9f0a",
};
export const FONT = '-apple-system, BlinkMacSystemFont, "SF Pro Display", "Segoe UI", Inter, sans-serif';

export const useSpring = (at: number, damping = 14) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  return spring({ frame: frame - f(at), fps, config: { damping } });
};

/** Sample-aligned stems: count-in, then guitar out, then vocals out, then a fade. */
export const Soundtrack = () => {
  const x = 0.35;
  const out = (fr: number) => interpolate(fr, [f(27), f(30)], [1, 0], clamp);
  const win = (fr: number, a: number, b: number) =>
    interpolate(fr, [f(a - x), f(a + x)], [0, 1], clamp) * interpolate(fr, [f(b - x), f(b + x)], [1, 0], clamp);
  return (
    <>
      <Audio src={staticFile("demo/countin.mp3")} volume={(fr) => win(fr, -1, T.guitar) * out(fr)} />
      <Audio src={staticFile("demo/noguitar.mp3")} volume={(fr) => win(fr, T.guitar, T.vocals) * out(fr)} />
      <Audio src={staticFile("demo/novocals.mp3")} volume={(fr) => win(fr, T.vocals, 99) * out(fr)} />
    </>
  );
};

export const Window = ({ src, style }: { src: string; style?: React.CSSProperties }) => (
  <div style={{ borderRadius: 18, overflow: "hidden", boxShadow: "0 2px 6px rgba(0,0,0,.08), 0 30px 90px rgba(0,0,0,.22)", ...style }}>
    <Img src={staticFile(src)} style={{ width: "100%", display: "block" }} />
  </div>
);

/** The site address as a browser would show it: globe, muted https://, the domain. */
export const UrlPill = ({ size, style }: { size: number; style?: React.CSSProperties }) => (
  <div style={{ display: "flex", alignItems: "center", gap: size * 0.4, fontSize: size, fontWeight: 600, color: C.text,
    padding: `${size * 0.4}px ${size * 0.87}px ${size * 0.4}px ${size * 0.65}px`, borderRadius: size, background: C.card,
    border: `2px solid ${C.border}`, boxShadow: "0 8px 30px rgba(0,0,0,.12)", ...style }}>
    <svg width={size * 0.96} height={size * 0.96} viewBox="0 0 24 24" fill="none" stroke={C.accent} strokeWidth="2" strokeLinecap="round">
      <circle cx="12" cy="12" r="10" /><path d="M2 12h20M12 2a15 15 0 0 1 0 20M12 2a15 15 0 0 0 0 20" />
    </svg>
    <span><span style={{ color: C.muted, fontWeight: 400 }}>https://</span>count-in.app</span>
  </div>
);

export const EndCard = ({ tagline, bg = C.bg, fg = C.muted }: { tagline: string; bg?: string; fg?: string }) => {
  const s = useSpring(T.end);
  const s2 = useSpring(T.end + 0.5);
  return (
    <AbsoluteFill style={{ background: bg, alignItems: "center", justifyContent: "center", fontFamily: FONT, color: C.text }}>
      <Img src={staticFile("icon.png")} style={{ width: 200, height: 200, transform: `scale(${s})` }} />
      <div style={{ fontSize: 120, fontWeight: 800, letterSpacing: -3, marginTop: 24, opacity: s }}>Count-In</div>
      <div style={{ fontSize: 42, color: fg, marginTop: 8, maxWidth: "88%", textAlign: "center", opacity: s2 }}>{tagline}</div>
      <UrlPill size={46} style={{ marginTop: 48, opacity: s2, transform: `translateY(${(1 - s2) * 30}px)` }} />
    </AbsoluteFill>
  );
};
