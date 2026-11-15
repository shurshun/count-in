import { Composition, Still } from "remotion";
import { FPS, LEN } from "./shared";
import { Landscape } from "./Landscape";
import { Poster } from "./Poster";
import { Vertical } from "./Vertical";

const size = { width: 1920, height: 1080 };

export const Root = () => (
  <>
    <Composition id="promo-en" component={Landscape} defaultProps={{ lang: "en" as const }} durationInFrames={LEN} fps={FPS} {...size} />
    <Composition id="promo-ru" component={Landscape} defaultProps={{ lang: "ru" as const }} durationInFrames={LEN} fps={FPS} {...size} />
    <Composition id="vertical" component={Vertical} durationInFrames={LEN} fps={FPS} width={1080} height={1920} />
    <Still id="poster-en" component={Poster} defaultProps={{ lang: "en" as const }} {...size} />
    <Still id="poster-ru" component={Poster} defaultProps={{ lang: "ru" as const }} {...size} />
  </>
);
