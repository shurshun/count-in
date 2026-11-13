# Count-In

Inserts a drum-stick count-in before the first downbeat of a song (mp3, FLAC, ALAC, WAV). The last click lands exactly one click before the song's "one".

## Install

```bash
brew install ffmpeg            # or packaging/build_ffmpeg.sh for the bundled LGPL build
uv sync
packaging/fetch_models.sh      # beat_this final0 (81 MB), BS-RoFormer SW (700 MB)
```

## Use

```bash
uv run countin song.mp3 --bars 1 --meter 4/4 --out song_countin.mp3
uv run countin *.mp3 --dry-run            # table: BPM, downbeat, confidence
uv run countin-verify song_countin.mp3 --last-click 2.22526
```

| Flag | Meaning |
|---|---|
| `--bars 1\|2` | count-in length |
| `--meter 4/4\|3/4\|6/8` | 6/8 clicks six eighths over two dotted-quarter beats |
| `--bpm`, `--first-beat SEC` | manual correction |

When the detector counts 6 beats in a 6/8 bar or 8 in a 4/4 bar, the click period gets rescaled from the bar length.
| `--tempo-x2`, `--tempo-half` | fix a doubled or halved tempo |
| `--pattern file.mid` | MIDI note positions as the rhythm, played with the stick samples; length rounds up to whole bars |
| `--pre-roll keep\|trim` | `keep` mixes clicks over any intro audio; `trim` first cuts leading silence |
| `--volume DB` | count-in gain |
| `--out`, `--format mp3\|flac\|alac\|wav` | output format: mp3 320k, FLAC level 8, ALAC, WAV; default follows the source |
| `--style even\|buildup` | buildup: second half twice as dense, the last three hits louder |
| `--guitar-db DB`, `--vocals-db DB` | stem levels for a backing track: `-12` keeps a hint, `--guitar-db=-inf` removes it |

Samples live in `samples/stick_hi.wav` (bar start) and `samples/stick_lo.wav`. Missing files get synthesized in memory. Silence before a sample's attack gets cut.

## How timing stays exact

- Clicks get placed on beats found in the same decoded stream, so the source's encoder delay cannot move them.
- mp3 output is 320 kbps CBR with a LAME tag, so players drop the encoder delay.
- Each click rounds to a sample from its own float time.
- beat_this works in 20 ms frames. Peak interpolation plus a line fit over 16 beats gets the grid phase below a frame.

`countin-verify` prints two numbers:

- **vs planned** finds the last click by cross-correlation and compares it to the planned sample. This checks the codec path.
- **click vs downbeat-1** re-runs the detector on the output. It includes the detector's own error of a few ms.

## Guitar attenuation

BS-RoFormer SW (jarredou) splits the song into six stems in one pass: bass, drums, other, vocals, guitar, piano.
The output is the original minus the removed share of the guitar stem, so the other instruments stay untouched.

- Model code is vendored in `src/countin/core/roformer` (MIT, from python-audio-separator).
- A 4-minute track takes about 2.5 minutes on an M-series Mac (MPS). Weights: 700 MB.
- Mono and stereo only.

## App

```bash
uv run countin-gui [files...]
```

- Drop tracks or folders on the window or the Dock icon. Each gets analyzed in the background.
- The orange marker is the first downbeat. Drag it; it snaps to detected beats, Alt places it freely. Wheel zooms, dragging pans.
- Count-in styles: even clicks, build-up (second half twice as dense, the last three hits louder), or a MIDI pattern.
- Guitar and vocal sliders turn those stems down; both come from one separation pass.
- Space or "Прослушать" plays the count-in plus 10 s of the song. With a stem slider down at start, that excerpt gets separated once; then both sliders change the mix live.
- Long jobs show stage, percent and time left, and can be cancelled between chunks.
- On start the app asks GitHub for the latest release in a background thread (10 s timeout). A newer version shows a banner with a download link for this platform.
- "Сохранить" and "Сохранить все" write next to each source: `<name>_countin.<ext>`, or `<name>_backing_guitar-off_vocals-6dB.<ext>` for a backing track.

## Build

```bash
uv run pyinstaller packaging/countin-app.spec --noconfirm --distpath build/dist --workpath build/work  # Count-In.app
uv run pyinstaller packaging/countin.spec --noconfirm --distpath build/dist --workpath build/work      # CLI
```

Models and samples go into the bundle. ffmpeg comes from `bin/` when present, else from PATH.
`packaging/build_ffmpeg.sh` builds an LGPL ffmpeg/ffprobe (audio only, LAME static) into `bin/`.
CI (`.github/workflows/build.yml`) builds a Windows x64 zip and a macOS arm64 .app on every push; tags `v*` publish them as a release. To release: bump `version` in `pyproject.toml`, then tag `v<version>`; the update check compares against that version. Builds are unsigned; the spec has `codesign_identity` and `entitlements_file` for notarized builds.

## Tests

```bash
uv run pytest            # all
uv run pytest -m 'not slow'
```

## Known limits

- Live tempo drift and rubato. The grid fits the first 16 beats only.
- Intros without a beat (ambient, feedback) push the first downbeat to where the beat starts. Use `--first-beat` to pick another one.
- Half-time intros may make the detector place the bar line on the wrong beat. Check the downbeat list and correct it with `--first-beat`.
