import { AbsoluteFill } from "remotion";
import { C, FONT, Window } from "./shared";
import { COPY, Lang } from "./text";

/** What the site's player shows before anyone presses play. */
export const Poster = ({ lang }: { lang: Lang }) => (
  <AbsoluteFill style={{ background: C.accent, fontFamily: FONT, alignItems: "center" }}>
    <div style={{ marginTop: 120, fontSize: 210, fontWeight: 900, letterSpacing: -8, color: C.text }}>Count-In</div>
    <div style={{ position: "absolute", width: 1500, top: 470, transform: "rotate(-4deg)" }}>
      <Window src={COPY[lang].shot} />
    </div>
  </AbsoluteFill>
);
