import { C } from "./shared";

export type Lang = "en" | "ru";

// One line per bar, cut on the beat; `at` is in bars after the song's "one" (fractions allowed)
export type Line = { bars?: number; at?: "guitar" | "vocals"; text: string; accent?: string };

export const COPY: Record<Lang, {
  shot: string; size: [number, number]; syllables: string[]; lines: Line[]; tagline: string;
}> = {
  en: {
    shot: "screenshots/main-light-en.png",
    size: [2360, 1600],
    syllables: ["Ev", "ery ", "song ", "de", "serves ", "a ", "Count-", "In"],
    tagline: "Free for macOS and Windows · works offline",
    lines: [
      { bars: 0, text: "Count-In" },
      { bars: 1, text: "Finds the tempo", accent: "tempo" },
      { bars: 2, text: "Finds the “one”", accent: "“one”" },
      { bars: 3, text: "Clicks on the exact sample", accent: "exact" },
      { bars: 4, text: "4/4, 3/4, 6/8 or your MIDI", accent: "MIDI" },
      { bars: 5, text: "Press Space to preview", accent: "Space" },
      { at: "guitar", text: "Guitar out", accent: "out" },
      { at: "guitar", bars: 1, text: "Your turn to play", accent: "play" },
      { at: "vocals", text: "Vocals out", accent: "out" },
      { at: "vocals", bars: 0.9, text: "Offline and free", accent: "free" },
    ],
  },
  ru: {
    shot: "screenshots/main-light.png",
    size: [2402, 1600],
    syllables: ["У ", "каж", "дой ", "пес", "ни ", "свой ", "от", "счёт"],
    tagline: "Бесплатно для macOS и Windows · работает без интернета",
    lines: [
      { bars: 0, text: "Count-In" },
      { bars: 1, text: "Находит темп", accent: "темп" },
      { bars: 2, text: "Находит «раз»", accent: "«раз»" },
      { bars: 3, text: "Клики точно в сэмпл", accent: "точно" },
      { bars: 4, text: "4/4, 3/4, 6/8 или свой MIDI", accent: "MIDI" },
      { bars: 5, text: "Пробел — прослушать", accent: "Пробел" },
      { at: "guitar", text: "Без гитары", accent: "Без" },
      { at: "guitar", bars: 1, text: "Теперь играешь ты", accent: "ты" },
      { at: "vocals", text: "Без вокала", accent: "Без" },
      { at: "vocals", bars: 0.9, text: "Офлайн и бесплатно", accent: "бесплатно" },
    ],
  },
};

export const ACCENT_ON_ORANGE = "#fff";
export const PANEL = { bg: C.accent, fg: "#1d1d1f" };
