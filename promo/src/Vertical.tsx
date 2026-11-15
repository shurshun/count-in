import { AbsoluteFill, Easing, Img, interpolate, staticFile } from "remotion";
import { C, EndCard, FONT, ONE, Soundtrack, T, clamp, f } from "./shared";
import { FOCUS } from "./zoom";
import { Intro } from "./Promo";
import { Flash, Footer, Header, pop, useBeat, useLine } from "./verticalParts";

// 9:16 cut for Reels and Shorts. The whole window stays in view, a ring marks the control
// and a magnified lens of it pops out below. The accent word carries the line.

const WIN = 760, LENS = { w: 820, h: 430 }, RINGS = { x: 540, y: 560 };

export const Vertical = () => {
  const { frame, copy, at, i, line } = useLine();
  const { k, pulse } = useBeat();
  const intro = frame < f(ONE);
  const [W, H] = copy.size;
  const winH = (WIN * H) / W;

  const p = interpolate(frame, [f(at[i]), f(at[i]) + 14], [0, 1], { ...clamp, easing: Easing.bezier(0.2, 0.8, 0.2, 1) });
  const prev = FOCUS[Math.max(0, i - 1)], cur = FOCUS[i];
  const mix = (k: number) => prev[k] + (cur[k] - prev[k]) * p;
  const [fx, fy, z] = [mix(0), mix(1), mix(2)];

  // The lens shows only while a control is in focus; without it the window takes the room
  const show = (prev[2] > 1 ? 1 : 0) * (1 - p) + (cur[2] > 1 ? 1 : 0) * p;
  const s = 0.33 * z;
  const x = Math.min(0, Math.max(LENS.w - W * s, LENS.w / 2 - fx * W * s));
  const y = Math.min(0, Math.max(LENS.h - H * s, LENS.h / 2 - fy * H * s));

  const [l, r] = line.text.split(line.accent!).map((t) => t.trim());
  const t = pop(frame, at[i], 0, 12);
  const small = { fontSize: 78, fontWeight: 800, letterSpacing: -1.5, color: C.text, opacity: t };
  return (
    <AbsoluteFill style={{ background: C.accent, fontFamily: FONT }}>
      <Soundtrack />
      {intro && <Intro syllables={copy.syllables} orange />}
      {intro ? null : frame < f(T.end) ? (
        <>
          {[1, 2, 3].map((n) => {
            const d = n * 420 * (1 + 0.05 * pulse);
            return <div key={n} style={{ position: "absolute", left: RINGS.x - d / 2, top: RINGS.y - d / 2, width: d, height: d, borderRadius: d,
              border: `${6 + 10 * pulse}px solid rgba(255,255,255,.28)` }} />;
          })}
          <Header />
          <>
              <div style={{ position: "absolute", left: (1080 - WIN) / 2, top: 265, width: WIN, height: winH, transformOrigin: "50% 0",
                transform: `translateY(${(1 - show) * 90}px) scale(${pop(frame, ONE) * (1.24 - 0.24 * show)}) perspective(1800px) rotateX(10deg) rotateZ(-3deg)` }}>
                <Img src={staticFile(copy.shot)} style={{ width: WIN, borderRadius: 18, boxShadow: "0 30px 80px rgba(120,60,0,.4)" }} />
                <div style={{ position: "absolute", left: fx * WIN - 46, top: fy * winH - 46, width: 92, height: 92, borderRadius: 46, opacity: show,
                  border: `8px solid ${C.accent}`, boxShadow: "0 0 0 5px #fff, 0 8px 24px rgba(0,0,0,.35)", scale: `${1 + 0.18 * pulse}` }} />
              </div>
              <div style={{ position: "absolute", left: 110, top: 700, width: LENS.w, height: LENS.h, borderRadius: 44, overflow: "hidden", background: "#fff",
                border: "10px solid #fff", boxShadow: "0 30px 80px rgba(120,60,0,.5)", transformOrigin: `${fx * 100}% 0`,
                transform: `scale(${show}) rotate(1.5deg)` }}>
                <Img src={staticFile(copy.shot)} style={{ position: "absolute", left: 0, top: 0, width: W, height: H, maxWidth: "none", transformOrigin: "0 0",
                  transform: `translate(${x}px, ${y}px) scale(${s})` }} />
                <Flash />
              </div>
          </>
          <div style={{ position: "absolute", left: 70, right: 150, top: 1150, height: 320, display: "flex", flexDirection: "column", justifyContent: "center" }}>
            <div key={i}>
                {l && <div style={small}>{l}</div>}
                <div style={{ fontSize: 150, fontWeight: 900, letterSpacing: -6, lineHeight: 0.95, color: "#fff", transformOrigin: "0 60%",
                  transform: `scale(${0.6 + 0.4 * t})`, opacity: Math.min(1, t * 2) }}>{line.accent}</div>
                {r && <div style={small}>{r}</div>}
            </div>
          </div>
          <div style={{ position: "absolute", left: 70, top: 1492, display: "flex", gap: 14 }}>
            {[0, 1, 2, 3].map((n) => <div key={n} style={{ width: 16, height: 16, borderRadius: 8, background: n === k ? C.text : "rgba(0,0,0,.18)" }} />)}
          </div>
          <Footer />
        </>
      ) : <EndCard tagline={copy.tagline} bg={C.accent} fg="rgba(0,0,0,.6)" />}
    </AbsoluteFill>
  );
};
