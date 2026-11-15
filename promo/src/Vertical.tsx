import { AbsoluteFill, interpolate, useCurrentFrame } from "remotion";
import { Intro, starts } from "./Promo";
import { BEAT, C, EndCard, FONT, ONE, Soundtrack, T, clamp, f, useSpring } from "./shared";
import { COPY, Line } from "./text";
import { ZoomShot } from "./zoom";

// 9:16 cut for Reels and Shorts. App on top, the line below it.
// Safe zones: the platforms cover ~250 px at the top, ~450 px at the bottom and ~150 px on the right,
// so the line sits in 1160–1470 and keeps clear of the right edge.

const W = 1080;
const TEXT_TOP = 1160, TEXT_BOTTOM = 1470;

const Accent = ({ line }: { line: Line }) => {
  if (!line.accent || !line.text.includes(line.accent)) return <>{line.text}</>;
  const [l, r] = line.text.split(line.accent);
  return <>{l}<span style={{ color: C.accent }}>{line.accent}</span>{r}</>;
};

const Beats = () => {
  const frame = useCurrentFrame();
  const n = Math.floor((frame / 30 - ONE) / BEAT);
  return (
    <div style={{ display: "flex", gap: 14 }}>
      {[0, 1, 2, 3].map((k) => (
        <div key={k} style={{ width: 16, height: 16, borderRadius: 8, background: ((n % 4) + 4) % 4 === k ? C.accent : C.border }} />
      ))}
    </div>
  );
};

/** Huge dark type with the orange accent, the beat dots under it. */
const LineText = ({ line, at }: { line: Line; at: number }) => {
  const s = useSpring(at, 11);
  return (
    <div style={{ position: "absolute", left: 70, right: 150, top: TEXT_TOP, height: TEXT_BOTTOM - TEXT_TOP, display: "flex", flexDirection: "column",
      justifyContent: "center", gap: 30 }}>
      <div style={{ fontSize: 104, fontWeight: 900, letterSpacing: -4, lineHeight: 1, color: C.text, transform: `scale(${0.9 + 0.1 * s})`,
        transformOrigin: "left center", opacity: s }}>
        <Accent line={line} />
      </div>
      <Beats />
    </div>
  );
};

/** Light stage, the window tilted in 3D on top, the line below. */
export const Vertical = () => {
  const frame = useCurrentFrame();
  const copy = COPY.en;
  const at = starts(copy.lines);
  let i = 0;
  while (i + 1 < at.length && frame >= f(at[i + 1])) i++;
  const enter = interpolate(frame, [f(ONE), f(ONE) + 12], [1, 0], clamp);
  const app = { left: 60, top: 250, w: W - 120, h: 860 };
  return (
    <AbsoluteFill style={{ background: C.bg2, fontFamily: FONT }}>
      <Soundtrack />
      {frame < f(ONE) && <Intro syllables={copy.syllables} />}
      {frame >= f(ONE) && frame < f(T.end) && (
        <>
          <div style={{ position: "absolute", left: app.left, top: app.top, width: app.w, height: app.h, borderRadius: 32, overflow: "hidden",
            background: "#fff", boxShadow: "0 30px 80px rgba(0,0,0,.18)",
            transform: `translateY(${-enter * 300}px) perspective(1800px) rotateX(8deg) rotateZ(-2deg)` }}>
            <ZoomShot w={app.w} h={app.h} src={copy.shot} size={copy.size} starts={at} />
          </div>
          <LineText key={i} line={copy.lines[i]} at={at[i]} />
        </>
      )}
      {frame >= f(T.end) && <EndCard tagline={copy.tagline} />}
    </AbsoluteFill>
  );
};
