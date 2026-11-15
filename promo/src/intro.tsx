import { AbsoluteFill, interpolate, useCurrentFrame } from "remotion";
import { BAR, C, EIGHTHS, FONT, ONE, QUARTERS, T, clamp, f } from "./shared";
import { Line } from "./text";

/** Start of each line in seconds: bars after the song's "one", or after the guitar or vocals drop. */
export const starts = (lines: Line[]) => lines.map((l) => (l.at ? T[l.at] : ONE) + (l.bars ?? 0) * BAR);

/** Digits on the quarter clicks, then the hook line one syllable per eighth click, on orange. */
export const Intro = ({ syllables }: { syllables: string[] }) => {
  const frame = useCurrentFrame();
  const q = QUARTERS.filter((t) => frame >= f(t)).length;
  const e = EIGHTHS.filter((t) => frame >= f(t)).length;
  const last = e ? EIGHTHS[e - 1] : QUARTERS[Math.max(0, q - 1)];
  const kick = interpolate(frame - f(last), [0, 5], [1.08, 1], clamp);
  return (
    <AbsoluteFill style={{ background: C.accent, alignItems: "center", justifyContent: "center", fontFamily: FONT, color: C.text }}>
      {e === 0 ? (
        <div style={{ fontSize: 360, fontWeight: 900, transform: `scale(${kick})` }}>{q || ""}</div>
      ) : (
        <div style={{ fontSize: 150, fontWeight: 900, letterSpacing: -4, maxWidth: "90%", textAlign: "center", lineHeight: 1.05, transform: `scale(${kick})` }}>
          {syllables.map((s, i) => <span key={i} style={{ color: i < e ? (i >= 6 ? "#fff" : C.text) : "transparent" }}>{s}</span>)}
        </div>
      )}
    </AbsoluteFill>
  );
};
