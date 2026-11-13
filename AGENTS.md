# Count-In: maintainer notes

Count-In inserts a drum count-in before a song's first downbeat and can turn guitar and vocals down for a backing track. It ships as a desktop app (PySide6) for macOS arm64 and Windows x64, plus a CLI. The README covers usage; this file covers how it works and where to change it.

## Layout

| Path | Role |
|---|---|
| `src/countin/core/io.py` | ffmpeg/ffprobe I/O, output formats and names, `resource()` for bundle paths |
| `src/countin/core/analyze.py` | beat and downbeat detection, grid fit, attack snap, confidence |
| `src/countin/core/render.py` | click grids (even, build-up, MIDI), sample-accurate mixing |
| `src/countin/core/stems.py` | BS-RoFormer SW separation and stem attenuation |
| `src/countin/core/roformer/` | vendored BS-RoFormer model code (MIT, see its LICENSE) |
| `src/countin/gui.py` | the app: window, waveform, jobs, live preview player |
| `src/countin/update.py` | GitHub release check |
| `src/countin/cli.py`, `verify.py` | CLI and the timing check tool |
| `packaging/` | PyInstaller spec, ffmpeg build, model download |
| `assets/icons/{light,dark}/` | app icons: ICO, ICNS, iconset, PNG 16–1024, raster master |
| `.github/workflows/build.yml` | Windows and macOS builds, smoke test, releases |

## Pipeline

1. **Decode.** `io.decode` pipes float32 PCM out of ffmpeg at the native rate. Clicks get placed on beats found in this same stream, so the source's encoder delay never matters.
2. **Detect.** `analyze.detect` runs beat_this (`final0.ckpt`, 50 fps) on the first 90 s. Peaks get a parabolic sub-frame refinement.
3. **Fit the grid.** `fit_grid` gives each beat a slot index from the median interval, then fits a least-squares line over 16 slots. Slot indexing survives half-time intros; the line averages out the 20 ms frame quantization.
4. **Snap.** `snap_onset` moves the downbeat to the sample-exact attack within ±25 ms. Of several attacks it takes the one nearest the grid.
5. **Bar phase and confidence.** Later downbeats vote on which beat starts the bar. Disagreement yields `alt_downbeat` and a note. `beat_period` rescales the period when the detector counts eighths in 6/8 or half time in 4/4.
6. **Separate (optional).** `stems.separate` runs BS-RoFormer SW in 9.3 s chunks with a Hamming overlap-add, overlap 2. It returns six stems; only guitar and vocals are used.
7. **Attenuate.** `stems.attenuate` subtracts `(1 - gain) * stem` from the original. Subtraction keeps the other instruments untouched instead of re-summing stems with their artifacts.
8. **Render.** `render.render` adds the click samples. Every click rounds to a sample from its own float time, so error never accumulates. Silence gets prepended when the song starts too early to hold the count-in.
9. **Encode.** `io.encode` writes MP3 320k (LAME tag), FLAC, ALAC or WAV and copies tags and cover art from the source.

## Invariants

- The last click lands exactly one click before the downbeat. `countin-verify` checks it: "vs planned" must stay at 0.00 ms.
- Stems come back sample-aligned with the input and of its shape. `test_stems_sum_back_to_the_mix_without_shift` guards it.
- Clicks never clip the song. `render` only counts overs the clicks add.
- Nothing downloads at run time. Models ship in the bundle; `update.check` is the only network call.

## Why these technologies

| Choice | Reason | Rejected |
|---|---|---|
| beat_this | best open beat/downbeat tracker, MIT code and weights | madmom: CC BY-NC-SA models, unmaintained |
| BS-RoFormer SW, one pass | six stems incl. guitar; keeps screamed vocals out of the guitar stem; about 2.5 min per 4-min song on MPS | Demucs htdemucs_6s: leaked vocals into guitar; two-pass RoFormer + Demucs: slower and worse |
| vendored RoFormer code | audio-separator pulled librosa, numba, onnx (~300 MB) and fetched files from the network | the audio-separator package |
| ffmpeg as a subprocess | every format, tags and cover art; arguments as lists, so any path is safe | Python codec bindings |
| LGPL ffmpeg build | GPL builds cannot ship in a closed bundle; LAME and mpg123 are LGPL | Homebrew ffmpeg (GPL, dynamic libs) |
| PySide6-Essentials | native look, LGPL, smallest Qt wheel | full PySide6 (adds WebEngine etc.) |
| sounddevice for preview | stems mix block by block, so sliders work live | afplay/winsound: no live mixing; QtMultimedia: needs PySide6-Addons |
| PyInstaller onedir | best torch support; onedir starts without unpacking 1.3 GB | onefile; Nuitka (slow, fragile with torch) |

## App internals (`gui.py`)

- **Threads.** `Job` (`QRunnable`) runs work in two single-thread pools: one for analysis, one for preview and save, so they never compete for a model twice. `job.report()` sends progress and raises `Cancelled` when the user cancels. Separation reports once per chunk, which makes cancel take a few seconds.
- **Signals.** Workers only emit signals; slots are bound methods of `QObject`s created on the UI thread, so Qt queues them onto it. Do not connect worker signals to lambdas.
- **Waveform.** `Wave` caches the waveform layer in a pixmap keyed by view and size. Repaints during playback only draw lines over it. A Python loop per repaint starved the audio callback and made preview crackle; keep the cache.
- **Preview.** `preview_job` returns the song with clicks plus aligned guitar and vocal stems. `Player` mixes them in the PortAudio callback with the current slider gains (`latency="high"`, 2048-frame blocks). Excerpt stems are cached per track in `stem_cache` as `(first sample, stems)`; a save stores full-track stems there too.
- **Progress.** Time left comes from the chunk rate since the stage began, excluding the first chunk that carries model loading.
- **One track.** `Window.open_files` takes the first audio file it is given and replaces the current track; a stale analysis gets cancelled and its result ignored.
- **Files from Finder.** `App.event` handles `QFileOpenEvent` for files dropped on the Dock icon.
- **Space.** An application event filter plays or stops on Space unless the key goes to a text field or spin box, or a modal dialog is open.
- **Preferences.** `PrefsDialog` saves to `QSettings` at once: `output/dir` (empty means next to the source), `output/group` and the `output/artist|year|album` levels, `appearance/icon`. `io.organized_dir` builds the folder from source tags; `io.safe_name` keeps names valid on Windows.
- **Status bar.** File format from `io.describe`, save status, the update button and the version.
- **Theme.** `style_sheet()` fills colour tokens in `STYLE` for light or dark appearance.
- **App icon.** The preferences dialog offers light (default), dark and "follow the app theme". The choice lives in `QSettings` under `appearance/icon`; a missing or unknown value means light. `apply_icon()` sets the window and Dock icon at run time and reruns on `colorSchemeChanged`, which only matters in the follow-theme mode. Finder and Explorer always show the built-in light ICNS/ICO; switching those would need separate work.

## Packaging

- `io.resource()` resolves `models/`, `samples/` and `bin/` against `sys._MEIPASS` in a bundle and the project root in development.
- `io.tool()` finds ffmpeg/ffprobe in `bin/` by full path; nothing relies on `PATH`.
- `packaging/countin.spec` builds one bundle with two executables sharing its libraries: `Count-In` (app) and `countin` (CLI). It bundles `models/final0.ckpt`, `models/separator/`, `samples/`, `bin/`, the light and dark PNGs from `assets/icons/` and the `countin` package metadata (the update check reads the version from it).
- `packaging/build_ffmpeg.sh` builds a static, audio-only LGPL ffmpeg on macOS. ffmpeg 7+ needs the `fd` protocol for `-` pipes. Windows CI downloads a BtbN LGPL build instead.
- `packaging/fetch_models.sh` downloads the weights and checks their sha256; `build_ffmpeg.sh` checks the ffmpeg tarball. Windows CI pins a dated BtbN build by hash; BtbN prunes old autobuilds, so a 404 there means moving the URL and hash to a newer one. CI caches `models/` keyed by the fetch script.
- The windowed Windows build has no stderr; `packaging/countin_app.py` points it at devnull before anything prints.

## CI and releases

- Pull requests that touch code build both platforms, run the fast tests, then run the bundled CLI on 20 s of synthetic clicks with separation and mp3 export. A broken bundle fails here, before packaging. Docs-only PRs and pushes to `main` build nothing.
- Speed: uv and macOS ffmpeg are cached; archives use zip level 1, which halves the libraries while the weights barely shrink. Higher levels save about 5% for four times the packing time.
- The app smoke test starts the bundled app offscreen with `COUNTIN_SMOKE=1`; it quits after two seconds and fails when the bundled icon is missing.
- Release: bump `version` in `pyproject.toml`, tag `v<version>`. Builds check that the tag matches the version. A separate `release` job runs only after both builds pass and is the only job with write access; it attaches `Count-In-windows-x64.zip` and `Count-In-macos-arm64.zip`. `update.asset_suffix()` matches those names.
- Errors: the app logs to `count-in.log` in its data folder (`~/Library/Application Support/Count-In/Count-In` on macOS, `%APPDATA%\Count-In\Count-In` on Windows). Job failures, analysis errors and unhandled exceptions land there.
- Builds are unsigned. The specs have `codesign_identity` and `entitlements_file` placeholders for notarization.

## Working on it

- Python tooling is uv: `uv sync`, `uv run pytest -m 'not slow'`, `uv run countin-gui`.
- Commits need a DCO sign-off (`git commit -s`). Work on a branch and open a PR.
- Test the timing path with `countin-verify` after touching `render.py`, `io.py` or `analyze.py`.
- After touching `stems.py` or the RoFormer code, run the slow test (`uv run pytest`).
- New output format: add it to `io.FORMATS` and `io._codec_args`, then extend `test_lossless_roundtrip_is_sample_exact`.
- New count-in pattern: add a grid function next to `buildup_grid` in `render.py`, a segment in `gui.py` and a choice in `cli.py`.

## Known limits

- The grid fits the first 16 beats only; live tempo drift and rubato are not followed.
- Intros without a beat push the first downbeat to where the beat starts; the user moves the marker.
- Separation on Windows runs on the CPU and takes several times longer than on Apple Silicon.
- The license of the BS-RoFormer SW weights is not stated by the author.
