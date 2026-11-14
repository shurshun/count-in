// Language follows the system unless chosen; theme is light unless chosen
const root = document.documentElement;
const pref = k => { try { return localStorage.getItem(k); } catch { return null; } };
const save = (k, v) => { try { localStorage.setItem(k, v); } catch {} };
root.lang = pref("lang") || (navigator.language.startsWith("ru") ? "ru" : "en");
root.dataset.theme = pref("theme") || "light";

document.addEventListener("click", e => {
  if (e.target.closest("[data-toggle=lang]")) save("lang", root.lang = root.lang === "ru" ? "en" : "ru");
  if (e.target.closest("[data-toggle=theme]")) save("theme", root.dataset.theme = root.dataset.theme === "dark" ? "light" : "dark");
});

// Demo player. Clips are sample-aligned: all four play at once and the buttons only move the gain.
const DEMO = {
  duration: 32,
  downbeat: 3.965,
  clicks: [0, 0.496, 0.991, 1.487, 1.983, 2.23, 2.478, 2.726, 2.974, 3.222, 3.469, 3.717],
  peaks: [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.36, 0.19, 0.08, 0.36, 0.2, 0.37, 0.43, 0.43, 0.41, 0.46, 0.43, 0.4, 0.39, 0.35, 0.31, 0.4, 0.46, 0.47, 0.42, 0.41, 0.42, 0.4, 0.38, 0.4, 0.42, 0.42, 0.4, 0.42, 0.41, 0.43, 0.43, 0.44, 0.45, 0.39, 0.41, 0.39, 0.41, 0.41, 0.38, 0.41, 0.42, 0.4, 0.38, 0.42, 0.46, 0.48, 0.5, 0.47, 0.4, 0.5, 0.46, 0.41, 0.4, 0.44, 0.44, 0.41, 0.4, 0.47, 0.4, 0.38, 0.41, 0.39, 0.42, 0.42, 0.45, 0.45, 0.44, 0.47, 0.43, 0.43, 0.43, 0.44, 0.43, 0.44, 0.44, 0.41, 0.41, 0.41, 0.46, 0.46, 0.44, 0.44, 0.48, 0.51, 0.5, 1.0, 0.59, 0.51, 0.48, 0.5, 1.0, 0.58, 0.48, 0.54, 0.48, 0.51, 0.51, 0.48, 0.52, 0.57, 1.0, 0.46, 0.52, 0.55, 0.56, 0.56, 0.61, 0.56, 0.55, 0.65, 1.0, 0.41, 0.56, 0.62, 0.58, 0.53, 0.52, 0.55, 0.57, 0.74, 0.91, 0.53, 0.53, 0.56, 0.85, 0.81, 0.49, 0.51, 0.54, 0.84, 0.77, 0.47, 0.52, 0.57, 0.95, 0.74, 0.55, 0.51, 0.57, 0.96, 0.71, 0.54, 0.52, 0.53, 1.0, 0.64, 0.52, 0.54, 0.56, 1.0, 0.69, 0.57, 0.52, 0.54, 1.0, 0.75, 0.69, 0.72, 0.59, 0.83, 0.46, 0.84, 0.96, 0.53, 1.0, 0.61, 0.6, 0.6, 0.61, 0.8, 0.4, 0.37, 0.27, 0.44, 0.69, 0.61, 0.52, 0.5, 0.73, 0.62, 0.39, 0.39, 0.25, 0.41, 0.38, 0.37, 0.49, 0.48, 0.66, 0.47, 0.31, 0.57, 0.48, 0.47, 0.34, 0.22, 0.3, 0.2, 0.69, 0.34, 0.24, 0.38, 0.35, 0.61, 0.6, 0.3, 0.39, 0.29, 0.81, 0.49, 0.24, 0.38, 0.31, 0.59, 0.55, 0.5, 0.57, 0.52, 0.8, 0.39, 0.46, 0.63, 0.61, 0.47, 0.34, 0.47, 0.59, 0.52, 0.79, 0.37, 0.43, 0.45, 0.45, 0.34, 0.3, 0.42, 0.31, 0.58, 0.5, 0.31, 0.37, 0.34, 0.32, 0.37, 0.47, 0.47, 0.31, 0.75, 0.53, 0.42, 0.47, 0.43, 0.62, 0.62, 0.56, 0.41, 0.28, 0.78, 0.52, 0.44, 0.56, 0.47, 0.46, 0.45, 0.55, 0.61, 0.56, 0.71, 0.48, 0.43, 0.41, 0.22, 0.19, 0.21, 0.25, 0.21, 0.09, 0.34, 0.19, 0.18, 0.13, 0.08, 0.06, 0.04, 0.03, 0.0],
  variants: [
    ["original", "Original", "Оригинал"],
    ["countin", "Count-in", "С отсчётом"],
    ["noguitar", "No guitar", "Без гитары"],
    ["novocals", "No vocals", "Без вокала"],
  ],
};
const ICON_PLAY = '<svg viewBox="0 0 24 24"><path d="M7 4.5v15l13-7.5z"/></svg>';
const ICON_PAUSE = '<svg viewBox="0 0 24 24"><path d="M6 4h4.5v16H6zM13.5 4H18v16h-4.5z"/></svg>';

function player(el) {
  const N = DEMO.peaks.length, W = N * 3;
  // One path per layer keeps the DOM small
  const bars = `<path d="${DEMO.peaks.map((p, i) => {
    const h = Math.max(2, p * 80);
    return `M${i * 3} ${(96 - h) / 2 + 4}h2v${h}h-2z`;
  }).join("")}"/>`;
  const clicks = `<path d="${DEMO.clicks.map(t => `M${t / DEMO.duration * W} 22V82`).join("")}"/>`;
  el.innerHTML = `
    <div class="top">
      <button class="play" type="button" aria-label="Play">${ICON_PLAY}</button>
      <div class="title">The First Clear Mile<small><span lang="en">Demo song · 121 BPM · 2-bar build-up</span><span lang="ru">Демо-песня · 121 BPM · два такта с нарастанием</span></small></div>
      <span class="time">0:00 / 0:32</span>
    </div>
    <div class="wave">
      <svg viewBox="0 0 ${W} 104" preserveAspectRatio="none">
        <g class="clicks">${clicks}</g>
        <g class="bars">${bars}</g>
        <clipPath id="done"><rect class="cut" width="0" height="104"/></clipPath>
        <g class="bars done" clip-path="url(#done)">${bars}</g>
      </svg>
      <div class="one" style="left:${DEMO.downbeat / DEMO.duration * 100}%">1</div>
      <div class="head" style="left:0"></div>
    </div>
    <div class="seg">${DEMO.variants.map(([v, en, ru]) =>
      `<button type="button" data-v="${v}" aria-pressed="${v === "countin"}"><span lang="en">${en}</span><span lang="ru">${ru}</span></button>`).join("")}</div>`;

  const q = s => el.querySelector(s);
  const playBtn = q(".play"), wave = q(".wave"), cut = q(".cut"), head = q(".head"), time = q(".time");
  let ctx, buffers, gains, sources = [], current = "countin", offset = 0, startedAt = 0, playing = false;
  const fmt = t => Math.floor(t / 60) + ":" + String(Math.floor(t % 60)).padStart(2, "0");
  const position = () => playing ? Math.min(ctx.currentTime - startedAt, DEMO.duration) : offset;

  // Downloads start on the first scroll, pointer move, touch or key after load, so they never compete
  // with the page itself; decoding waits for the click that creates the AudioContext
  let files = null;
  const fetchFiles = () => files = files || Promise.all(DEMO.variants.map(([v]) =>
    fetch(el.dataset.src + v + ".mp3")
      .then(r => { if (!r.ok) throw new Error(r.status + " " + r.url); return r.arrayBuffer(); })))
    .catch(e => { files = null; throw e; });
  addEventListener("load", () => {
    if (navigator.connection?.saveData) return;
    const events = ["scroll", "pointermove", "touchstart", "keydown"];
    const go = () => {
      events.forEach(e => removeEventListener(e, go));
      fetchFiles().catch(() => {});
    };
    events.forEach(e => addEventListener(e, go, { once: true, passive: true }));
  });

  async function load() {
    ctx = ctx || new AudioContext();
    const data = await fetchFiles();
    buffers = await Promise.all(data.map(b => ctx.decodeAudioData(b.slice(0))));
    gains = DEMO.variants.map(([v]) => {
      const g = ctx.createGain();
      g.gain.value = v === current ? 1 : 0;
      g.connect(ctx.destination);
      return g;
    });
  }
  function start(at) {
    sources = buffers.map((b, i) => {
      const s = ctx.createBufferSource();
      s.buffer = b; s.connect(gains[i]); s.start(0, at);
      return s;
    });
    sources[0].onended = () => stop(0);
    startedAt = ctx.currentTime - at; playing = true; playBtn.innerHTML = ICON_PAUSE; playBtn.ariaLabel = "Pause";
    requestAnimationFrame(loop);
  }
  function stop(at) {
    sources.forEach(s => { s.onended = null; s.stop(); });
    sources = []; playing = false; offset = at; playBtn.innerHTML = ICON_PLAY; playBtn.ariaLabel = "Play";
    draw();
  }

  playBtn.onclick = async () => {
    if (!buffers) {
      playBtn.disabled = true;
      try { await load(); } catch (e) { buffers = null; console.error(e); return; } finally { playBtn.disabled = false; }
    }
    await ctx.resume();
    playing ? stop(position()) : start(offset);
  };
  q(".seg").onclick = e => {
    const b = e.target.closest("button");
    if (!b) return;
    current = b.dataset.v;
    el.querySelectorAll(".seg button").forEach(x => x.setAttribute("aria-pressed", x === b));
    wave.classList.toggle("no-clicks", current === "original");
    if (gains) DEMO.variants.forEach(([v], i) => gains[i].gain.setTargetAtTime(v === current ? 1 : 0, ctx.currentTime, 0.015));
  };
  wave.onclick = e => {
    const r = wave.getBoundingClientRect();
    const at = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)) * DEMO.duration;
    if (playing) { stop(at); start(at); } else { offset = at; draw(); }
  };
  // Redraws per frame only while playing
  function draw() {
    const t = position(), f = t / DEMO.duration;
    cut.setAttribute("width", f * W);
    head.style.left = f * 100 + "%";
    time.textContent = fmt(t) + " / " + fmt(DEMO.duration);
  }
  function loop() {
    draw();
    if (playing) requestAnimationFrame(loop);
  }
}
document.querySelectorAll("[data-player]").forEach(player);
