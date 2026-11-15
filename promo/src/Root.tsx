import { Composition, Still } from "remotion";
import { FPS, LEN } from "./shared";
import { Promo } from "./Promo";
import { Poster } from "./Poster";
import { Vertical } from "./Vertical";

const size = { width: 1920, height: 1080 };

export const Root = () => (
  <>
    <Composition id="promo-en" component={Promo} defaultProps={{ lang: "en" as const }} durationInFrames={LEN} fps={FPS} {...size} />
    <Composition id="promo-ru" component={Promo} defaultProps={{ lang: "ru" as const }} durationInFrames={LEN} fps={FPS} {...size} />
    {(["card", "caption", "dark"] as const).map((style) => (
      <Composition key={style} id={`vertical-${style}`} component={Vertical} defaultProps={{ style }} durationInFrames={LEN} fps={FPS} width={1080} height={1920} />
    ))}
    <Still id="poster-en" component={Poster} defaultProps={{ lang: "en" as const }} {...size} />
    <Still id="poster-ru" component={Poster} defaultProps={{ lang: "ru" as const }} {...size} />
  </>
);
