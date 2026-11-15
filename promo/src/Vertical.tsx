import { AbsoluteFill, Img, interpolate, staticFile, useCurrentFrame } from "remotion";
import { Intro, starts } from "./Promo";
import { BEAT, C, EndCard, FONT, ONE, Soundtrack, T, UrlPill, clamp, f, useSpring } from "./shared";
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
  return <>{l}<span style={{ color: "#fff" }}>{line.accent}</span>{r}</>;
};

const Beats = () => {
  const frame = useCurrentFrame();
  const n = Math.floor((frame / 30 - ONE) / BEAT);
  return (
    <div style={{ display: "flex", gap: 14 }}>
      {[0, 1, 2, 3].map((k) => (
        <div key={k} style={{ width: 16, height: 16, borderRadius: 8, background: ((n % 4) + 4) % 4 === k ? C.text : "rgba(0,0,0,.18)" }} />
      ))}
    </div>
  );
};

/** Huge dark type with a white accent, the beat dots under it. */
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

const APPLE = "M12.152 6.896c-.948 0-2.415-1.078-3.96-1.04-2.04.027-3.91 1.183-4.961 3.014-2.117 3.675-.546 9.103 1.519 12.09 1.013 1.454 2.208 3.09 3.792 3.039 1.52-.065 2.09-.987 3.935-.987 1.831 0 2.35.987 3.96.948 1.637-.026 2.676-1.48 3.676-2.948 1.156-1.688 1.636-3.325 1.662-3.415-.039-.013-3.182-1.221-3.22-4.857-.026-3.04 2.48-4.494 2.597-4.559-1.429-2.09-3.623-2.324-4.39-2.376-2-.156-3.675 1.09-4.61 1.09zM15.53 3.83c.843-1.012 1.4-2.427 1.245-3.83-1.207.052-2.662.805-3.532 1.818-.78.896-1.454 2.338-1.273 3.714 1.338.104 2.715-.688 3.559-1.701";
const WINDOWS = "M2 2h9.5v9.5H2zM12.5 2H22v9.5h-9.5zM2 12.5h9.5V22H2zM12.5 12.5H22V22h-9.5z";
const Os = ({ d }: { d: string }) => <svg width="40" height="40" viewBox="0 0 24 24"><path d={d} fill={C.text} /></svg>;

// The header already names the app, so the first line says what it does
const FIRST: Line = { text: "A count-in for every song", accent: "every song" };

/** App name above the window. */
const Header = () => {
  const s = useSpring(ONE, 16);
  return (
    <div style={{ position: "absolute", top: 110, left: 0, right: 0, display: "flex", justifyContent: "center", alignItems: "center", gap: 20, opacity: s }}>
      <Img src={staticFile("icon.png")} style={{ width: 84, height: 84 }} />
      <div style={{ fontSize: 64, fontWeight: 900, letterSpacing: -2, color: C.text }}>Count-In</div>
    </div>
  );
};

/** Platforms and the address under the line. */
const Footer = () => {
  const s = useSpring(ONE + 0.4, 16);
  return (
    <div style={{ position: "absolute", top: 1560, left: 0, right: 0, display: "flex", flexDirection: "column", alignItems: "center", gap: 22, opacity: s }}>
      <div style={{ display: "flex", alignItems: "center", gap: 18, fontSize: 40, fontWeight: 700, color: C.text }}>
        <Os d={APPLE} /><Os d={WINDOWS} /><span>Free for macOS and Windows</span>
      </div>
      <UrlPill size={38} />
    </div>
  );
};

/** Orange stage: name on top, the window tilted in 3D, the line, platforms at the bottom. */
export const Vertical = () => {
  const frame = useCurrentFrame();
  const copy = COPY.en;
  const at = starts(copy.lines);
  let i = 0;
  while (i + 1 < at.length && frame >= f(at[i + 1])) i++;
  const enter = interpolate(frame, [f(ONE), f(ONE) + 12], [1, 0], clamp);
  const app = { left: 60, top: 250, w: W - 120, h: 860 };
  return (
    <AbsoluteFill style={{ background: C.accent, fontFamily: FONT }}>
      <Soundtrack />
      {frame < f(ONE) && <Intro syllables={copy.syllables} orange />}
      {frame >= f(ONE) && frame < f(T.end) && (
        <>
          <div style={{ position: "absolute", left: app.left, top: app.top, width: app.w, height: app.h, borderRadius: 32, overflow: "hidden",
            background: "#fff", boxShadow: "0 30px 80px rgba(120,60,0,.35)",
            transform: `translateY(${-enter * 300}px) perspective(1800px) rotateX(8deg) rotateZ(-2deg)` }}>
            <ZoomShot w={app.w} h={app.h} src={copy.shot} size={copy.size} starts={at} />
          </div>
          <LineText key={i} line={i === 0 ? FIRST : copy.lines[i]} at={at[i]} />
          <Header />
          <Footer />
        </>
      )}
      {frame >= f(T.end) && <EndCard tagline={copy.tagline} bg={C.accent} fg="rgba(0,0,0,.6)" />}
    </AbsoluteFill>
  );
};
