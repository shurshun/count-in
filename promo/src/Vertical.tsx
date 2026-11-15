import { AbsoluteFill, interpolate, useCurrentFrame } from "remotion";
import { Intro, starts } from "./Promo";
import { BEAT, C, EndCard, FONT, ONE, Soundtrack, T, clamp, f, useSpring } from "./shared";
import { COPY, Line } from "./text";
import { ZoomShot } from "./zoom";

// 9:16 cut for Reels and Shorts. App on top, the line below it.
// Safe zones: the platforms cover ~250 px at the top, ~450 px at the bottom and ~150 px on the right,
// so the line sits in 1160–1450 and keeps clear of the right edge.

export type VStyle = "card" | "caption" | "dark";

const W = 1080, H = 1920;
const TEXT_TOP = 1160, TEXT_BOTTOM = 1470;

const Accent = ({ line, color, box }: { line: Line; color: string; box?: string }) => {
  if (!line.accent || !line.text.includes(line.accent)) return <>{line.text}</>;
  const [l, r] = line.text.split(line.accent);
  const span = box
    ? <span style={{ background: box, color, padding: "0 14px", borderRadius: 14 }}>{line.accent}</span>
    : <span style={{ color }}>{line.accent}</span>;
  return <>{l}{span}{r}</>;
};

const Beats = ({ fg, dim }: { fg: string; dim: string }) => {
  const frame = useCurrentFrame();
  const n = Math.floor((frame / 30 - ONE) / BEAT);
  return (
    <div style={{ display: "flex", gap: 14 }}>
      {[0, 1, 2, 3].map((k) => (
        <div key={k} style={{ width: 16, height: 16, borderRadius: 8, background: ((n % 4) + 4) % 4 === k ? fg : dim }} />
      ))}
    </div>
  );
};

/** Light page, the app in a rounded card, an orange card with the line under it. */
const CardLine = ({ line, at }: { line: Line; at: number }) => {
  const s = useSpring(at, 12);
  return (
    <div style={{ position: "absolute", left: 60, right: 150, top: TEXT_TOP, height: TEXT_BOTTOM - TEXT_TOP, borderRadius: 36, background: C.accent,
      padding: "0 56px", display: "flex", flexDirection: "column", justifyContent: "center", gap: 28, transform: `translateY(${(1 - s) * 60}px)`, opacity: s }}>
      <div style={{ fontSize: 92, fontWeight: 900, letterSpacing: -3, lineHeight: 1.02, color: C.text }}><Accent line={line} color="#fff" /></div>
      <Beats fg={C.text} dim="rgba(0,0,0,.18)" />
    </div>
  );
};

/** The app fills the top, the line pops word by word as a caption sticker. */
const CaptionLine = ({ line, at }: { line: Line; at: number }) => {
  const frame = useCurrentFrame();
  const words = line.text.split(" ");
  return (
    <div style={{ position: "absolute", left: 60, right: 150, top: TEXT_TOP, height: TEXT_BOTTOM - TEXT_TOP, display: "flex", flexWrap: "wrap",
      alignContent: "center", gap: "18px 20px" }}>
      {words.map((w, k) => {
        const t = f(at + k * BEAT / 2);
        const s = interpolate(frame, [t, t + 5], [0.6, 1], clamp);
        const o = interpolate(frame, [t, t + 2], [0, 1], clamp);
        const hot = !!line.accent && line.accent.split(" ").includes(w);
        return (
          <span key={k} style={{ fontSize: 96, fontWeight: 900, letterSpacing: -3, lineHeight: 1.15, padding: "4px 22px", borderRadius: 18,
            background: hot ? C.accent : "#fff", color: C.text, boxShadow: "0 10px 30px rgba(0,0,0,.25)", transform: `scale(${s})`, opacity: o }}>{w}</span>
        );
      })}
    </div>
  );
};

/** Dark stage, the window tilted in 3D, huge white type with an orange accent. */
const DarkLine = ({ line, at }: { line: Line; at: number }) => {
  const s = useSpring(at, 11);
  return (
    <div style={{ position: "absolute", left: 70, right: 150, top: TEXT_TOP, height: TEXT_BOTTOM - TEXT_TOP, display: "flex", flexDirection: "column",
      justifyContent: "center", gap: 30 }}>
      <div style={{ fontSize: 104, fontWeight: 900, letterSpacing: -4, lineHeight: 1, color: "#fff", transform: `scale(${0.9 + 0.1 * s})`, transformOrigin: "left center", opacity: s }}>
        <Accent line={line} color={C.accent} />
      </div>
      <Beats fg={C.accent} dim="#333" />
    </div>
  );
};

export const Vertical = ({ style }: { style: VStyle }) => {
  const frame = useCurrentFrame();
  const copy = COPY.en;
  const at = starts(copy.lines);
  let i = 0;
  while (i + 1 < at.length && frame >= f(at[i + 1])) i++;
  const enter = interpolate(frame, [f(ONE), f(ONE) + 12], [1, 0], clamp);
  const dark = style === "dark";
  const Line = style === "card" ? CardLine : style === "caption" ? CaptionLine : DarkLine;

  // App box: a card inside the safe zone, full-bleed for captions, a tilted card on the dark stage
  const app = style === "caption"
    ? { left: 0, top: 0, w: W, h: 1240, radius: 0, tilt: "" }
    : { left: 60, top: 250, w: W - 120, h: 860, radius: 32, tilt: dark ? "perspective(1800px) rotateX(8deg) rotateZ(-2deg)" : "" };

  return (
    <AbsoluteFill style={{ background: dark ? "#111113" : style === "caption" ? "#1d1d1f" : C.bg2, fontFamily: FONT }}>
      <Soundtrack />
      {frame < f(ONE) && <Intro syllables={copy.syllables} dark={dark || style === "caption"} />}
      {frame >= f(ONE) && frame < f(T.end) && (
        <>
          <div style={{ position: "absolute", left: app.left, top: app.top, width: app.w, height: app.h, borderRadius: app.radius, overflow: "hidden",
            background: "#fff", boxShadow: app.radius ? "0 30px 80px rgba(0,0,0,.25)" : "none",
            transform: `translateY(${-enter * 300}px) ${app.tilt}` }}>
            <ZoomShot w={app.w} h={app.h} src={copy.shot} size={copy.size} starts={at} />
            {style === "caption" && <AbsoluteFill style={{ background: "linear-gradient(transparent 70%, #1d1d1f)" }} />}
          </div>
          <Line key={i} line={copy.lines[i]} at={at[i]} />
        </>
      )}
      {frame >= f(T.end) && <EndCard tagline={copy.tagline} />}
    </AbsoluteFill>
  );
};
