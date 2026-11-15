import { AbsoluteFill, interpolate, useCurrentFrame } from "remotion";
import { BAR, BEAT, C, EIGHTHS, EndCard, FONT, ONE, QUARTERS, Soundtrack, T, clamp, f, useSpring } from "./shared";
import { ACCENT_ON_ORANGE, COPY, Lang, Line, PANEL } from "./text";
import { ZoomShot } from "./zoom";

// 30 s promo: clicks build the intro line, then an orange text panel beside the zooming app, one line per bar

// Landscape: text panel on the left. Vertical (Reels, Shorts): text panel on top.
const PANEL_W = 860;
const PANEL_H = 760;

export const starts = (lines: Line[]) => lines.map((l) => (l.at ? T[l.at] : ONE) + (l.bars ?? 0) * BAR);

const Words = ({ text, accent }: { text: string; accent?: string }) => {
  if (!accent || !text.includes(accent)) return <>{text}</>;
  const [l, r] = text.split(accent);
  return <>{l}<span style={{ color: ACCENT_ON_ORANGE }}>{accent}</span>{r}</>;
};

const Intro = ({ syllables }: { syllables: string[] }) => {
  const frame = useCurrentFrame();
  const q = QUARTERS.filter((t) => frame >= f(t)).length;
  const e = EIGHTHS.filter((t) => frame >= f(t)).length;
  const last = e ? EIGHTHS[e - 1] : QUARTERS[Math.max(0, q - 1)];
  const kick = interpolate(frame - f(last), [0, 5], [1.08, 1], clamp);
  return (
    <AbsoluteFill style={{ background: C.bg, alignItems: "center", justifyContent: "center", fontFamily: FONT, color: C.text }}>
      {e === 0 ? (
        <div style={{ fontSize: 360, fontWeight: 900, transform: `scale(${kick})` }}>{q || ""}</div>
      ) : (
        <div style={{ fontSize: 150, fontWeight: 900, letterSpacing: -4, maxWidth: "90%", textAlign: "center", lineHeight: 1.05, transform: `scale(${kick})` }}>
          {syllables.map((s, i) => <span key={i} style={{ color: i < e ? (i >= 6 ? C.accent : C.text) : "transparent" }}>{s}</span>)}
        </div>
      )}
    </AbsoluteFill>
  );
};

const Panel = ({ line, at, vertical }: { line: Line; at: number; vertical?: boolean }) => {
  const frame = useCurrentFrame();
  const s = useSpring(at, 12);
  const n = Math.floor((frame / 30 - ONE) / BEAT);
  return (
    <AbsoluteFill style={{ width: vertical ? 1080 : PANEL_W, height: vertical ? PANEL_H : 1080, background: PANEL.bg, justifyContent: "center", padding: "0 80px", fontFamily: FONT }}>
      <div style={{ fontSize: vertical ? 120 : 108, fontWeight: 900, letterSpacing: -4, lineHeight: 1.02, color: PANEL.fg, transform: `translateX(${(1 - s) * -80}px)`, opacity: s }}>
        <Words text={line.text} accent={line.accent} />
      </div>
      <div style={{ position: "absolute", left: 80, bottom: vertical ? 70 : 90, display: "flex", gap: 16 }}>
        {[0, 1, 2, 3].map((k) => (
          <div key={k} style={{ width: 18, height: 18, borderRadius: 9, background: ((n % 4) + 4) % 4 === k ? PANEL.fg : "rgba(0,0,0,.18)" }} />
        ))}
      </div>
    </AbsoluteFill>
  );
};

export const Promo = ({ lang, vertical }: { lang: Lang; vertical?: boolean }) => {
  const frame = useCurrentFrame();
  const copy = COPY[lang];
  const at = starts(copy.lines);
  let i = 0;
  while (i + 1 < at.length && frame >= f(at[i + 1])) i++;
  const enter = interpolate(frame, [f(ONE), f(ONE) + 12], [1, 0], clamp);
  return (
    <AbsoluteFill style={{ background: C.bg }}>
      <Soundtrack />
      {frame < f(ONE) && <Intro syllables={copy.syllables} />}
      {frame >= f(ONE) && frame < f(T.end) && (
        <>
          {vertical ? (
            <div style={{ position: "absolute", left: 0, top: PANEL_H, width: 1080, height: 1920 - PANEL_H, background: C.bg2, transform: `translateY(${enter * 400}px)` }}>
              <ZoomShot w={1080} h={1920 - PANEL_H} src={copy.shot} size={copy.size} starts={at} />
            </div>
          ) : (
            <div style={{ position: "absolute", left: PANEL_W, top: 0, width: 1920 - PANEL_W, height: 1080, background: C.bg2, transform: `translateX(${enter * 300}px)` }}>
              <ZoomShot w={1920 - PANEL_W} h={1080} src={copy.shot} size={copy.size} starts={at} />
            </div>
          )}
          <Panel key={i} line={copy.lines[i]} at={at[i]} vertical={vertical} />
        </>
      )}
      {frame >= f(T.end) && <EndCard tagline={copy.tagline} />}
    </AbsoluteFill>
  );
};
