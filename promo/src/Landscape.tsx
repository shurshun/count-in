import { LangContext, Lang } from "./text";
import { C, ONE } from "./shared";
import { Flash, pop, useBeat } from "./verticalParts";
import { Brand, Drop, Foot, Frame, Magnified, Ring, Shot, fit, useFocus } from "./landscapeParts";

// 16:9 site video. The line sits in a left column, the tilted window on the right, and a lens card of the control pops out under it.

const WIN = 940, LEFT = 900, LENS = { w: 780, h: 400 }, COL = 760;

const Scene = () => {
  const { frame, copy, at, i, fx, fy, z, show, before, accent, after } = useFocus();
  const { k, pulse } = useBeat();
  const shot = copy.shot.replace("main-light", `main-light-${i >= 8 ? "vocals" : i >= 6 ? "guitar" : "promo"}`);
  const winH = (WIN * copy.size[1]) / copy.size[0];
  const t = pop(frame, at[i]);
  const small = { fontSize: 70, fontWeight: 800, letterSpacing: -1.5, color: C.text, opacity: t };
  return (
    <>
      {[1, 2, 3].map((n) => {
        const d = n * 460 * (1 + 0.05 * pulse);
        return <div key={n} style={{ position: "absolute", left: 1370 - d / 2, top: 470 - d / 2, width: d, height: d, borderRadius: d,
          border: `${6 + 10 * pulse}px solid rgba(255,255,255,.28)` }} />;
      })}
      <Brand left={90} top={70} />

      <div style={{ position: "absolute", left: LEFT, top: 60, width: WIN, height: winH, transformOrigin: "50% 0",
        transform: `translateY(${(1 - show) * 130}px) scale(${pop(frame, ONE) * (1 - 0.2 * show)}) perspective(1800px) rotateX(10deg) rotateZ(-3deg)` }}>
        <Shot src={shot} w={WIN} />
        <Ring x={fx * WIN} y={fy * winH} pulse={pulse} opacity={show} />
      </div>
      <div style={{ position: "absolute", left: LEFT + (WIN - LENS.w) / 2 + 40, top: 560, width: LENS.w, height: LENS.h, borderRadius: 44, overflow: "hidden",
        background: "#fff", border: "10px solid #fff", boxShadow: "0 30px 80px rgba(120,60,0,.5)", transformOrigin: `${fx * 100}% 0`,
        transform: `scale(${show}) rotate(1.5deg)` }}>
        <Magnified src={shot} size={copy.size} w={LENS.w} h={LENS.h} fx={fx} fy={fy} s={0.3 * z} />
        <Flash />
      </div>

      <div style={{ position: "absolute", left: 90, top: 200, width: COL, height: 520, display: "flex", flexDirection: "column", justifyContent: "center" }}>
        <div key={i}>
          {before && <div style={small}>{before}</div>}
          <div style={{ fontSize: fit(accent, COL, 200), fontWeight: 900, letterSpacing: -6, lineHeight: 1, color: "#fff", whiteSpace: "nowrap",
            transformOrigin: "0 60%", transform: `scale(${0.6 + 0.4 * t})`, opacity: Math.min(1, t * 2) }}>{accent}</div>
          {after && <div style={small}>{after}</div>}
        </div>
      </div>
      <div style={{ position: "absolute", left: 94, top: 750, display: "flex", gap: 14 }}>
        {[0, 1, 2, 3].map((n) => <div key={n} style={{ width: 18, height: 18, borderRadius: 9, background: n === k ? C.text : "rgba(0,0,0,.18)" }} />)}
      </div>
      <Foot left={90} top={830} scale={0.95} />
      <Drop />
    </>
  );
};

export const Landscape = ({ lang }: { lang: Lang }) => (
  <LangContext.Provider value={lang}><Frame><Scene /></Frame></LangContext.Provider>
);
