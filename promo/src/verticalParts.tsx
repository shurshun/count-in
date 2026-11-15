import React from "react";
import { AbsoluteFill, Img, interpolate, spring, staticFile, useCurrentFrame } from "remotion";
import { starts } from "./intro";
import { BEAT, C, FPS, ONE, T, UrlPill, clamp, f } from "./shared";
import { useCopy } from "./text";

// Parts of the 9:16 cut. Safe zones: top 250, bottom 450, right 150 px.

/** The line on screen at the current frame. */
export const useLine = () => {
  const frame = useCurrentFrame();
  const copy = useCopy();
  const at = starts(copy.lines);
  let i = 0;
  while (i + 1 < at.length && frame >= f(at[i + 1])) i++;
  return { frame, copy, at, i, line: i === 0 ? copy.first : copy.lines[i] };
};

export const pop = (frame: number, at: number, delay = 0, damping = 12) =>
  spring({ frame: frame - f(at) - delay, fps: FPS, config: { damping } });

/** Beat of the bar (0–3) and a pulse that decays from 1 after each beat. */
export const useBeat = () => {
  const x = (useCurrentFrame() / FPS - ONE) / BEAT;
  const n = Math.floor(x);
  return { k: ((n % 4) + 4) % 4, pulse: Math.max(0, 1 - (x - n) * 2.5) };
};

/** White flash when the guitar and the vocals drop out. */
export const Flash = () => {
  const frame = useCurrentFrame();
  const o = Math.max(...[T.guitar, T.vocals].map((t) => interpolate(frame - f(t), [0, 2, 12], [0, 0.8, 0], clamp)));
  return <AbsoluteFill style={{ background: "#fff", opacity: o }} />;
};

export const Header = ({ color = C.text, style }: { color?: string; style?: React.CSSProperties }) => (
  <div style={{ position: "absolute", top: 110, left: 0, right: 0, display: "flex", justifyContent: "center", alignItems: "center", gap: 20, ...style }}>
    <Img src={staticFile("icon.png")} style={{ width: 84, height: 84 }} />
    <div style={{ fontSize: 64, fontWeight: 900, letterSpacing: -2, color }}>Count-In</div>
  </div>
);

const APPLE = "M12.152 6.896c-.948 0-2.415-1.078-3.96-1.04-2.04.027-3.91 1.183-4.961 3.014-2.117 3.675-.546 9.103 1.519 12.09 1.013 1.454 2.208 3.09 3.792 3.039 1.52-.065 2.09-.987 3.935-.987 1.831 0 2.35.987 3.96.948 1.637-.026 2.676-1.48 3.676-2.948 1.156-1.688 1.636-3.325 1.662-3.415-.039-.013-3.182-1.221-3.22-4.857-.026-3.04 2.48-4.494 2.597-4.559-1.429-2.09-3.623-2.324-4.39-2.376-2-.156-3.675 1.09-4.61 1.09zM15.53 3.83c.843-1.012 1.4-2.427 1.245-3.83-1.207.052-2.662.805-3.532 1.818-.78.896-1.454 2.338-1.273 3.714 1.338.104 2.715-.688 3.559-1.701";
const WINDOWS = "M2 2h9.5v9.5H2zM12.5 2H22v9.5h-9.5zM2 12.5h9.5V22H2zM12.5 12.5H22V22h-9.5z";

/** Platforms, the price and the address. */
export const Footer = ({ color = C.text, top = 1560 }: { color?: string; top?: number }) => {
  const copy = useCopy();
  return (
  <div style={{ position: "absolute", top, left: 0, right: 0, display: "flex", flexDirection: "column", alignItems: "center", gap: 10, fontSize: 40, fontWeight: 700, color }}>
    <div style={{ display: "flex", alignItems: "center", gap: 18 }}>
      {[APPLE, WINDOWS].map((d) => <svg key={d} width="40" height="40" viewBox="0 0 24 24"><path d={d} fill={color} /></svg>)}
      <span>{copy.available}</span>
    </div>
    <div>{copy.free}</div>
    <UrlPill size={38} style={{ marginTop: 12 }} />
  </div>
  );
};
