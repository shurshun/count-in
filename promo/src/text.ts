import { createContext, useContext } from "react";

export type Lang = "en" | "ru";

// One line per bar, cut on the beat; `at` is in bars after the song's "one" (fractions allowed)
export type Line = { bars?: number; at?: "guitar" | "vocals"; text: string; accent?: string };

export const COPY: Record<Lang, {
  shot: string; size: [number, number]; syllables: string[]; lines: Line[]; tagline: string;
  first: Line; available: string; free: string;
}> = {
  en: {
    shot: "screenshots/main-light-en.png",
    size: [2360, 1840],
    syllables: ["Ev", "ery ", "song ", "de", "serves ", "a ", "Count-", "In"],
    tagline: "Free for macOS and Windows · works offline",
    first: { text: "A count-in for every song", accent: "every song" },
    available: "Available for macOS and Windows", free: "For free",
    lines: [
      { bars: 0, text: "Count-In" },
      { bars: 1, text: "Finds the tempo", accent: "tempo" },
      { bars: 2, text: "Finds the “one”", accent: "“one”" },
      { bars: 3, text: "Clicks on the exact sample", accent: "exact" },
      { bars: 4, text: "4/4, 3/4, 6/8 or your MIDI", accent: "MIDI" },
      { bars: 5, text: "Hi-hat fills the pauses", accent: "Hi-hat" },
      { at: "guitar", text: "Guitar out", accent: "out" },
      { at: "guitar", bars: 1, text: "Slower, same pitch", accent: "same pitch" },
      { at: "vocals", text: "Vocals out", accent: "out" },
      { at: "vocals", bars: 0.9, text: "Offline and free", accent: "free" },
    ],
  },
  ru: {
    shot: "screenshots/main-light.png",
    size: [2462, 1840],
    syllables: ["У ", "каж", "дой ", "пес", "ни ", "свой ", "от", "счёт"],
    tagline: "Бесплатно для macOS и Windows · работает без интернета",
    first: { text: "Отсчёт перед любой песней", accent: "любой песней" },
    available: "Доступно для macOS и Windows", free: "Бесплатно",
    lines: [
      { bars: 0, text: "Count-In" },
      { bars: 1, text: "Находит темп", accent: "темп" },
      { bars: 2, text: "Находит «раз»", accent: "«раз»" },
      { bars: 3, text: "Клики точно в сэмпл", accent: "точно" },
      { bars: 4, text: "4/4, 3/4, 6/8 или свой MIDI", accent: "MIDI" },
      { bars: 5, text: "Хай-хэт в паузах — сам", accent: "Хай-хэт" },
      { at: "guitar", text: "Без гитары", accent: "гитары" },
      { at: "guitar", bars: 1, text: "Медленнее, тон тот же", accent: "тон тот же" },
      { at: "vocals", text: "Без вокала", accent: "вокала" },
      { at: "vocals", bars: 0.9, text: "Офлайн и бесплатно", accent: "бесплатно" },
    ],
  },
};

// Language of the composition being rendered; English unless a provider says otherwise
export const LangContext = createContext<Lang>("en");
export const useCopy = () => COPY[useContext(LangContext)];
