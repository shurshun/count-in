"""Grid, splice and codec-delay checks on synthetic signals with a known tempo and downbeat."""

import os
from pathlib import Path
import numpy as np
import soundfile as sf
import pytest

from countin.core import io, render as rd
from countin.core.analyze import Analysis, beat_period, fit_grid, snap_onset
from countin.verify import find_onset

SR = 44100


def impulse_song(t0: float, period: float, seconds: float, lead_silence: float = 0.0) -> np.ndarray:
    """Stereo song: optional silence, then a quiet pad, with a loud burst on every beat from t0."""
    n = int(seconds * SR)
    x = np.zeros((n, 2), np.float32)
    x[int(lead_silence * SR):] = 0.01
    for k in range(int((seconds - t0) / period)):
        s = int(round((t0 + k * period) * SR))
        x[s : s + 200] = 0.5
    return x


# --- grid ---

@pytest.mark.parametrize("meter,clicks", [("4/4", 4), ("3/4", 3), ("6/8", 6)])
def test_grid_ends_one_click_before_downbeat(meter, clicks):
    t0, period = 3.0, 0.5
    g = rd.click_grid(t0, period, bars=2, meter=meter)
    step = period / rd.METERS[meter][1]
    assert len(g) == 2 * clicks
    assert g[-1].time == pytest.approx(t0 - step)
    assert np.allclose(np.diff([c.time for c in g]), step)
    assert [c.accent for c in g] == [k % clicks == 0 for k in range(2 * clicks)]


def test_count_in_length_matches_formula():
    bpm, bars = 137.0, 2
    g = rd.click_grid(10.0, 60 / bpm, bars, "4/4")
    assert 10.0 - g[0].time == pytest.approx(bars * 4 * 60 / bpm)


def test_fit_grid_recovers_subframe_phase():
    rng = np.random.default_rng(0)
    t0, period = 1.2345, 60 / 141
    beats = t0 + np.arange(16) * period + rng.uniform(-0.01, 0.01, 16)  # 20 ms frame quantization
    p, phase, *_ = fit_grid(beats, 0)
    assert abs(phase - t0) < 0.004
    assert abs(60 / p - 141) < 1.5


def test_fit_grid_handles_half_time_intro():
    period = 0.32
    slots = [0, 2, 4, 6, *range(7, 20)]  # intro hits every other beat
    beats = 6.0 + np.array(slots) * period
    p, phase, _, share = fit_grid(beats, 0)
    assert p == pytest.approx(period) and phase == pytest.approx(6.0) and share == 1


def test_pattern_grid_stretches_midi(tmp_path):
    import mido

    mid = mido.MidiFile(ticks_per_beat=480)
    tr = mido.MidiTrack()
    mid.tracks.append(tr)
    for i, tick in enumerate([0, 480, 480, 480, 240]):  # beats 0, 1, 2, 3, 3.5
        tr.append(mido.Message("note_on", note=37, velocity=100, time=tick if i else 0))
    f = tmp_path / "p.mid"
    mid.save(f)

    g = rd.pattern_grid(f, first_downbeat=5.0, period=0.5, meter="4/4")
    assert [c.time for c in g] == pytest.approx([3.0, 3.5, 4.0, 4.5, 4.75])
    assert [c.accent for c in g] == [True, False, False, False, False]


def test_pattern_grid_quantizes_a_played_midi_onto_the_song_grid():
    # tests/data/count-in-played-96bpm.mid: an empty bar, then two bars played in by hand at 96 BPM, up to 30
    # ticks off the grid, kick and hi-hat together on each "1"
    f = Path(__file__).parent / "data" / "count-in-played-96bpm.mid"
    g = rd.pattern_grid(f, first_downbeat=10.0, period=0.5, meter="4/4")  # the song runs at 120 BPM
    assert [c.time for c in g] == pytest.approx([6.0, 7.0, 8.0, 8.5, 9.0, 9.25, 9.5, 9.75])
    assert [c.accent for c in g] == [True, False, True, False, False, False, False, False]
    assert g[-1].time < 10.0  # the pattern ends before the downbeat


# --- splice ---

def test_keep_pads_when_downbeat_is_too_early():
    t0, period = 0.3, 0.5
    song = impulse_song(t0, period, 4.0)
    clicks = rd.click_grid(t0, period, 1, "4/4")
    r = rd.render(song, SR, clicks, t0, "keep")
    expected = [int(round(c.time * SR)) + int(round(r.offset * SR)) for c in clicks]
    assert r.click_samples == expected
    assert r.click_samples[0] >= 0
    assert len(r.pcm) == len(song) + int(round(r.offset * SR))

    # The song stays untouched behind the pad, and its downbeat sits one click after the last click.
    downbeat = int(round((t0 + r.offset) * SR))
    assert downbeat - r.click_samples[-1] == pytest.approx(period * SR, abs=1)


def test_keep_overlays_without_pad_when_intro_is_long():
    t0, period = 5.0, 0.5
    song = impulse_song(t0, period, 8.0)
    r = rd.render(song, SR, rd.click_grid(t0, period, 1, "4/4"), t0, "keep")
    assert r.offset == 0 and len(r.pcm) == len(song)
    assert r.click_samples[0] == int(round(3.0 * SR))


def test_trim_cuts_only_leading_silence():
    t0, period = 4.0, 0.5
    song = impulse_song(t0, period, 7.0, lead_silence=3.5)
    r = rd.render(song, SR, rd.click_grid(t0, period, 1, "4/4"), t0, "trim")

    # 3.5 s of silence gone, count-in needs 2 s but only 0.5 s of audio is left, so 1.5 s gets padded.
    assert r.offset == pytest.approx(-2.0, abs=1 / SR)
    assert r.click_samples[0] == 0


def test_click_positions_round_independently():
    t0, period = 7.0, 60 / 133.3  # non-integer samples per beat
    clicks = rd.click_grid(t0, period, 2, "4/4")
    r = rd.render(impulse_song(t0, period, 9.0), SR, clicks, t0)
    assert r.click_samples == [int(round(c.time * SR)) for c in clicks]


# --- codec delay ---

def _click_track(sr: int, at: list[float], seconds: float) -> tuple[np.ndarray, np.ndarray]:
    _, lo = rd.load_samples(sr)
    x = np.zeros((int(seconds * sr), 2), np.float32)
    for t in at:
        s = int(round(t * sr))
        x[s : s + len(lo)] += lo[:, None] * 0.7
    return x, lo


def test_mp3_roundtrip_keeps_sample_position(tmp_path):
    at = [0.5, 1.0, 2.2222]
    x, lo = _click_track(SR, at, 3.0)
    f = tmp_path / "c.mp3"
    io.encode(x, io.AudioInfo(SR, 2), f)

    y, info = io.decode(f)
    assert abs(len(y) - len(x)) <= 1
    for t in at:
        found = find_onset(y.mean(axis=1), lo * 0.7, int(round(t * SR)), 3000)
        assert abs(found - round(t * SR)) <= 2, (t, found - round(t * SR))


def test_snap_onset_finds_attack_sample():
    x = np.full((SR, 2), 0.001, np.float32)
    x[int(0.5 * SR) + 37 :] = 0.4  # attack 37 samples after 0.5 s
    assert snap_onset(x, SR, 0.505) == pytest.approx(0.5 + 37 / SR, abs=1 / SR)


def test_snap_onset_ignores_sustained_audio():
    x = np.full((SR, 2), 0.3, np.float32)
    assert snap_onset(x, SR, 0.5) is None


# --- guitar ---

@pytest.mark.parametrize("db,left", [(-6.0, 10 ** (-6 / 20)), (float("-inf"), 0.0), (0.0, 1.0)])
def test_attenuate_scales_only_the_guitar(db, left):
    from countin.core.stems import attenuate

    rng = np.random.default_rng(1)
    guitar = rng.standard_normal((1000, 2)).astype(np.float32) * 0.2
    rest = rng.standard_normal((1000, 2)).astype(np.float32) * 0.2
    vocals = rng.standard_normal((1000, 2)).astype(np.float32) * 0.2
    out = attenuate(rest + guitar + vocals, {"guitar": guitar, "vocals": vocals}, {"guitar": db, "vocals": -6.0})
    assert np.allclose(out, rest + left * guitar + 10 ** (-6 / 20) * vocals, atol=1e-6)


@pytest.mark.slow
def test_stems_sum_back_to_the_mix_without_shift():
    """Over a chunk boundary and through resampling, the stems must add up to the input in place."""
    from countin.core.stems import separate

    sr = 48000  # forces the resample path
    rng = np.random.default_rng(0)
    x = np.zeros((sr * 12, 2), np.float32)  # longer than one 9.3 s chunk
    for t in (1.0, 9.0, 10.5):
        x[int(t * sr) : int(t * sr) + 2000] = rng.standard_normal((2000, 2)) * 0.3
    y = sum(separate(x, sr).values())
    assert y.shape == x.shape
    for t in (1.0, 9.0, 10.5):
        s0 = int(t * sr)
        seg_x, seg_y = x[s0 - 200 : s0 + 2200, 0], y[s0 - 200 : s0 + 2200, 0]
        lag = int(np.argmax(np.correlate(seg_y, seg_x[200:2200], "valid"))) - 200
        assert lag == 0, (t, lag)


def test_chunks_cover_the_track():
    from countin.core.stems import _chunk_starts

    for n in (5, 100, 1000, 1001):
        starts = _chunk_starts(n, 100, 50)
        assert starts[0] == 0 and starts[-1] + 100 >= n and all(b - a <= 50 for a, b in zip(starts, starts[1:]))


# --- formats ---

@pytest.mark.parametrize("ext,bits", [(".flac", 16), (".flac", 24), (".m4a", 16), (".m4a", 24), (".wav", 16), (".wav", 24)])
def test_lossless_roundtrip_is_sample_exact(tmp_path, ext, bits):
    x, lo = _click_track(SR, [0.5, 1.2345], 2.0)
    f = tmp_path / f"c{ext}"
    io.encode(x, io.AudioInfo(SR, 2, codec="flac", bits=bits), f)

    y, info = io.decode(f)
    assert info.lossless and info.bits == bits
    assert len(y) == len(x)
    assert np.max(np.abs(y - x)) < 2 / 2 ** (bits - 1)


def test_default_format_follows_source():
    assert io.AudioInfo(SR, 2, codec="mp3").format == "mp3"
    assert io.AudioInfo(SR, 2, codec="alac", bits=16).format == "alac"
    assert io.AudioInfo(SR, 2, codec="pcm_s24le", bits=24).format == "flac"


def test_snap_onset_prefers_attack_nearest_the_grid():
    x = np.zeros((SR, 2), dtype=np.float32)
    x[int(0.482 * SR):, :] = 0.05  # quiet pick noise 18 ms early
    x[int(0.500 * SR):, :] = 0.8
    assert snap_onset(x, SR, 0.501) == pytest.approx(0.5, abs=2 / SR)


def test_snap_onset_works_at_file_start():
    x = np.zeros((SR, 2), dtype=np.float32)
    x[int(0.010 * SR):, :] = 0.5
    assert snap_onset(x, SR, 0.012) == pytest.approx(0.010, abs=2 / SR)


@pytest.mark.parametrize("bpb, meter_beats, factor", [(6, 2, 3), (8, 4, 2), (2, 4, 0.5), (4, 4, 1), (5, 4, 1)])
def test_beat_period_rescales_from_bar_length(bpb, meter_beats, factor):
    a = Analysis(beats=np.array([]), downbeats=np.array([]), bpm=120, first_downbeat=0, confidence=1,
                 activation=1, regularity=1, beats_per_bar=bpb)
    assert beat_period(a, meter_beats)[0] == pytest.approx(0.5 * factor)


def test_render_rejects_downbeat_past_end():
    x = np.zeros((SR, 2), dtype=np.float32)
    with pytest.raises(ValueError):
        rd.render(x, SR, rd.click_grid(1.5, 0.5, 1, "4/4"), 1.5)


def test_samples_start_at_their_attack(tmp_path):
    hit = np.r_[np.zeros(441), np.ones(100)].astype(np.float32)
    for name in ("stick_hi.wav", "stick_lo.wav"):
        sf.write(tmp_path / name, hit, SR, subtype="FLOAT")
    hi, lo = rd.load_samples(SR, tmp_path)
    assert hi[0] == lo[0] == 1.0


def test_buildup_matches_the_live_count_in():
    """2 bars of 4/4: quarters at -8..-5, eighths from -4 to -0.5, the last three louder."""
    b = rd.buildup_grid(10.0, 0.5, 2, "4/4")
    beats = [(x.time - 10.0) / 0.5 for x in b]
    assert beats == pytest.approx([-8, -7, -6, -5, -4, -3.5, -3, -2.5, -2, -1.5, -1, -0.5])
    assert [x.accent for x in b] == [True, False, False, False, True] + [False] * 7
    assert [x.gain > 1 for x in b] == [False] * 9 + [True] * 3


@pytest.mark.parametrize("meter", list(rd.METERS))
def test_buildup_ends_half_a_click_before_downbeat(meter):
    beats, sub = rd.METERS[meter]
    for bars in (1, 2):
        b = rd.buildup_grid(5.0, 0.6, bars, meter)
        assert b[-1].time == pytest.approx(5.0 - 0.3 / sub)
        assert b[0].time == pytest.approx(5.0 - bars * beats * 0.6)


def test_output_name_tells_the_backing_levels():
    song = Path("/m/Song.flac")
    assert io.output_path(song, "mp3", {}).name == "Song_countin.mp3"
    assert io.output_path(song, "flac", {"guitar": float("-inf"), "vocals": -6.0}).name == \
        "Song_backing_guitar-off_vocals-6dB.flac"


def test_update_check_offers_only_newer_releases():
    from countin import update

    base = f"https://github.com/{update.REPO}/releases/download/v0.2.0/"
    rel = {"tag_name": "v0.2.0",
           "assets": [{"name": "Count-In-windows-x64.zip", "browser_download_url": base + "Count-In-windows-x64.zip"},
                      {"name": "Count-In-macos-arm64.zip", "browser_download_url": base + "Count-In-macos-arm64.zip"}]}
    found = update.newer_release(rel, "0.1.0")
    assert found.version == "0.2.0" and found.url == base + f"Count-In-{update.asset_suffix()}"
    assert update.newer_release(rel, "0.2.0") is None
    assert update.newer_release(rel, "0.10.0") is None
    assert update.newer_release(rel, "0.2.0rc1").version == "0.2.0"
    assert update.newer_release({"tag_name": "v1.2.0"}, "1.2") is None

    # Links outside the repository fall back to its release page.
    evil = {"tag_name": "v9.0.0", "assets": [{"name": f"x-{update.asset_suffix()}",
                                             "browser_download_url": "https://evil.example/app.zip"}]}
    assert update.newer_release(evil, "0.1.0").url == f"https://github.com/{update.REPO}/releases/latest"

    # The asset's sha256 digest enables self-update; any other digest leaves it off.
    asset = {"name": f"Count-In-{update.asset_suffix()}", "browser_download_url": base + "x.zip"}
    def sha(**extra):
        return update.newer_release({"tag_name": "v0.2.0", "assets": [{**asset, **extra}]}, "0.1.0").sha256
    assert sha(digest="sha256:ab12") == "ab12" and sha(digest="md5:ab12") == "" and sha() == ""


def test_update_helper_swaps_the_bundle_after_the_app_exits(tmp_path):
    """Runs the real sh or cmd helper: it waits for the process, swaps folders and cleans up."""
    import subprocess
    import sys
    import time

    from countin import update

    root = tmp_path / ("Count-In" if sys.platform == "win32" else "Count-In.app")
    (root / "lib").mkdir(parents=True)
    (root / "lib" / "v").write_text("old")
    new = update.staging_dir(root) / "new" / root.name
    (new / "lib").mkdir(parents=True)
    (new / "lib" / "v").write_text("new")

    app = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(2)"])
    update.install_on_exit(root, new, relaunch=False, pid=app.pid)
    time.sleep(0.5)
    assert (root / "lib" / "v").read_text() == "old"  # still waiting for the app
    app.wait()
    for _ in range(100):
        if (root / "lib" / "v").read_text() == "new" and not update.staging_dir(root).exists():
            break
        time.sleep(0.2)
    log = update.HELPER_LOG.read_text(errors="replace") if update.HELPER_LOG.exists() else ""
    assert (root / "lib" / "v").read_text() == "new", log[-3000:]
    assert not update.staging_dir(root).exists() and not root.with_name(root.name + ".old").exists()

def test_icon_choice_is_light_or_dark_and_defaults_to_light(tmp_path):
    from PySide6.QtCore import QCoreApplication, QSettings

    from countin import gui
    from countin.core.io import resource

    QCoreApplication.setOrganizationName("CountInTest")
    QCoreApplication.setApplicationName("CountInTest")
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp_path))

    assert gui.icon_choice() == "light"
    for saved, used in (("dark", "dark"), ("light", "light"), ("system", "light"), ("neon", "light")):
        QSettings().setValue(gui.ICON_KEY, saved)
        assert gui.icon_choice() == used
    for v in ("light", "dark"):
        assert resource("assets", "icons", v, "png", "Count-In-256.png").exists()

def test_organized_dir_levels_and_unsafe_names():
    tags = {"artist": "AC/DC", "album": "Back in Black", "date": "1980-07-25"}
    root = Path("/out")
    assert io.organized_dir(root, tags, True, True, True) == root / "AC_DC" / "1980 - Back in Black"
    assert io.organized_dir(root, tags, True, False, True) == root / "AC_DC" / "Back in Black"
    assert io.organized_dir(root, tags, False, True, False) == root / "1980"
    assert io.organized_dir(root, {}, True, True, True) == root / "Неизвестный исполнитель" / "Без альбома"
    assert io.output_path(Path("/m/Song.mp3"), "flac", {}, root / "x").as_posix() == "/out/x/Song_countin.flac"
    assert io.safe_name('Live: "Best"?. ') == "Live_ _Best__"


def test_describe_lossy_and_lossless():
    mp3 = io.AudioInfo(sr=44100, channels=2, codec="mp3", bitrate=320000)
    flac = io.AudioInfo(sr=96000, channels=2, codec="flac", bits=24, bitrate=2304000)
    assert io.describe(mp3) == "MP3 · 320 kbps · 44.1 кГц · стерео"
    assert io.describe(flac) == "FLAC · 24 бит · 96 кГц · стерео · сжатие до 50%"


def test_every_ui_string_has_an_english_version():
    """Every literal passed to _() in the app has an English entry, so the English UI shows no Russian."""
    import ast

    from countin import i18n

    root = Path(__file__).resolve().parents[1] / "src" / "countin"
    missing = []
    for f in (root / "gui.py", root / "core" / "io.py"):
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "_" and node.args
                    and isinstance(node.args[0], ast.Constant) and node.args[0].value not in i18n.EN):
                missing.append(f"{f.name}:{node.lineno} {node.args[0].value!r}")

    # Constants translated where they are shown.
    from countin import gui
    parts = [t for _n, label, _i, tip in gui.PARTS for t in (label, tip)]
    for text in (*gui.ICON_CHOICES.values(), *gui.OUT_LEVELS.values(), gui.FORMAT_NAMES[None], i18n.LANGUAGES["system"],
                 *parts, *gui.JOB_WHY.values(), *gui.BUSY_WHY.values(), *gui.PHRASES, *gui.TIPS,
                 *(text for title, lines in gui.HELP for text in (title, *lines))):
        if text not in i18n.EN:
            missing.append(text)
    assert not missing, missing


def test_language_switch_changes_text():
    from countin import i18n

    info = io.AudioInfo(sr=48000, channels=1, codec="flac", bits=16, bitrate=500000)
    try:
        i18n.set_language("en")
        assert io.describe(info) == "FLAC · 16-bit · 48 kHz · mono · compressed to 65%"
        assert i18n._("Сохранено: {name}", name="a.mp3") == "Saved: a.mp3"
        i18n.set_language("ru")
        assert io.describe(info) == "FLAC · 16 бит · 48 кГц · моно · сжатие до 65%"
    finally:
        i18n.set_language("ru")


def test_mute_button_toggles_and_restores_level():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QSlider
    from countin.gui import MUTE, MuteButton

    _app = QApplication.instance() or QApplication([])
    s = QSlider(minimum=MUTE, maximum=0, value=-6)
    b = MuteButton(s, "")
    b.click()
    assert s.value() == MUTE and b.isChecked()
    b.click()
    assert s.value() == -6 and not b.isChecked()
    s.setValue(MUTE)
    assert b.isChecked()


def test_pause_bars_and_hi_hat_only_inside_the_song():
    from countin.core import zones as zn

    # Two 4/4 bars at 0.5 s, then a 3/4 bar at 0.6 s, then 4/4 again
    beats = [0, 0.5, 1, 1.5, 2, 2.5, 3, 3.5, 4, 4.6, 5.2, 5.8, 6.3, 6.8, 7.3]
    downs = [0, 2, 4, 5.8]
    bars = zn.bars_from(np.array(beats), np.array(downs), 8.0)
    assert [len(b.beats) for b in bars] == [4, 4, 3, 4]

    # a stray downbeat on the second beat of a bar folds back into that bar
    stray = zn.bars_from(np.array(beats), np.array([0, 0.5, 2, 4, 5.8]), 8.0)
    assert [(b.start, len(b.beats)) for b in stray[:2]] == [(0.0, 4), (2.0, 4)]

    # a pause mid-song: only the hi-hat on the bar's own beats, no stick count-in in front of it
    clicks = zn.zone_clicks(bars, zn.zones_from({2}), downbeat=0.0)
    assert [round(c.time, 3) for c in clicks] == [4.0, 4.6, 5.2]
    assert [c.accent for c in clicks] == [True, False, False]
    assert {c.voice for c in clicks} == {"hihat"}

    # an override plays a steady grid: bars 1-2 (2.0-5.8 s) at 120 BPM in 3/4
    z = zn.Zone(1, 2, bpm=120.0, beats=3)
    times = [round(c.time, 3) for c in zn.zone_clicks(bars, [z], downbeat=0.0)]
    assert times[:3] == [2.0, 2.5, 3.0]
    assert zn.describe(bars, z)[2:] == (120.0, 3)


def test_quiet_bars_and_runs():
    from countin.core import zones as zn

    sr = 1000
    x = np.full((4000, 2), 0.3, np.float32)
    x[1000:3000] = 0.001  # bars 1 and 2 nearly silent
    x[3500:] = 0.001  # a quiet ending is not a pause
    bars = [zn.Bar(float(i), float(i + 1), [float(i)]) for i in range(4)] + [zn.Bar(3.5, 4.0, [3.5])]
    bars[3] = zn.Bar(3.0, 3.5, [3.0])
    quiet = zn.quiet_bars(x, sr, bars, zn.level_db(x))
    assert quiet == {1, 2}
    assert zn.ranges(quiet | {0, 5}) == [(0, 2), (5, 5)]


def test_pause_runs_up_to_a_pickup_and_evens_out_a_lost_beat():
    from countin.core import zones as zn

    # A guitar-only intro: the detector gives bars of 6, 5 and 4 beats, then the band enters on the last
    # beat of bar 3 (a pickup) and plays 4/4 bars of 1.45 s from 6.37 s.
    sr = 1000
    x = np.full((12000, 2), 0.001, np.float32)
    x[6250:] = 0.3
    beats = [[0.44, 0.81, 0.99, 1.18, 1.56, 1.93], [2.06, 2.3, 2.42, 2.67, 3.05], [3.43, 3.8, 4.16, 4.54],
             [4.91, 5.28, 5.65, 6.02], [6.37, 6.73, 7.1, 7.46], [7.82, 8.18, 8.55, 8.91], [9.27, 9.63, 10.0, 10.36]]
    ends = [2.06, 3.43, 4.91, 6.37, 7.82, 9.27, 10.72]
    bars = [zn.Bar(b[0], e, b) for b, e in zip(beats, ends)]
    quiet = zn.quiet_bars(x, sr, bars, zn.level_db(x[6250:]))
    assert quiet == {0, 1, 2, 3}  # bar 3 counts: the pickup sits on its last beat

    (z,) = zn.zones_from(quiet)
    zb = zn.zone_bars(bars, z)
    assert [len(b.beats) for b in zb] == [4, 4, 4, 4]
    assert zb[0].start == 0.44 and zb[-1].end == 6.37
    _a, _b, bpm, n = zn.describe(bars, z)
    assert n == 4 and 160 < bpm < 164


def test_assets_search_order_and_copy(tmp_path, monkeypatch):
    from countin.core import assets

    monkeypatch.setattr(assets, "FILES", {"a.bin": (3, "", ""), "dir/b.bin": (2, "", "")})
    portable, data = tmp_path / "usb" / "assets", tmp_path / "data"
    monkeypatch.setattr(assets, "search_dirs", lambda: [portable, data])
    monkeypatch.setattr(assets, "data_dir", lambda: data)
    assert assets.missing() == ["a.bin", "dir/b.bin"]

    (portable / "dir").mkdir(parents=True)
    (portable / "a.bin").write_bytes(b"abc")
    (portable / "dir" / "b.bin").write_bytes(b"x")  # wrong size: incomplete
    assert assets.missing() == ["dir/b.bin"]
    assert assets.path("a.bin") == portable / "a.bin"

    (portable / "dir" / "b.bin").write_bytes(b"xy")
    assets.copy_from(portable)
    assert assets.has_all(data) and assets.path("dir") == portable / "dir"


def test_pauses_keep_manual_edits_and_join_without_extra_count_in():
    from countin.core import zones as zn

    beats = np.arange(0, 16, 0.5)
    bars = zn.bars_from(beats, beats[::4], 16.0)

    # the snapped "1" sits a few ms after the detected bar start: no second count-in, no lost hi-hat
    first = zn.zone_clicks(bars, [zn.Zone(0, 0)], downbeat=0.016)
    assert [c.voice for c in first] == ["hihat"] * 4

    # two pauses in a row run on as one hi-hat line
    joined = zn.zone_clicks(bars, [zn.Zone(1, 1), zn.Zone(2, 2)], downbeat=0.0)
    assert [c.voice for c in joined] == ["hihat"] * 8

    # an override describes itself as set; the automatic search keeps it
    edited = zn.Zone(3, 3, beats=6)
    assert zn.describe(bars, edited)[2:] == (120.0, 6)
    merged = zn.merge([edited], [zn.Zone(2, 4), zn.Zone(6, 6)])
    assert [(z.first, z.last) for z in merged] == [(3, 3), (6, 6)]


def test_waveform_peaks_stay_on_time_at_44_1_khz():
    from countin.gui import peaks_per_ms

    sr = 44100
    pcm = np.zeros((sr * 170, 2), np.float32)
    pcm[int(169.0 * sr)] = 1.0
    assert int(np.argmax(peaks_per_ms(pcm, sr))) == 169000


def test_pause_starts_right_after_the_last_hit_in_the_band_tempo():
    from countin.core import zones as zn

    # Four steady 4/4 bars of 2 s, the band's last hit on the "1" of bar 4, silence where the detector loses the
    # beat (bars of 5 and 6 beats), then the band again.
    sr = 1000
    x = np.full((24000, 2), 0.3, np.float32)
    x[8300:16000] = 0.001
    starts = [0, 2, 4, 6, 8, 10, 12.2, 14, 16, 18, 20, 22]
    bars = []
    for i, a in enumerate(starts):
        b = starts[i + 1] if i + 1 < len(starts) else 24.0
        n = {6: 5, 7: 6}.get(i, 4)
        bars.append(zn.Bar(a, b, list(a + (b - a) / n * np.arange(n))))
    quiet = zn.quiet_bars(x, sr, bars, zn.level_db(x[:8000]))
    assert min(quiet) == 4  # the bar with the last hit joins the pause

    (z,) = zn.zones_from(quiet)
    zb = zn.zone_bars(bars, z)
    assert [len(b.beats) for b in zb] == [4] * len(zb)  # the meter of the bars before the pause
    assert abs((zb[0].end - zb[0].start) - 2.0) < 0.1


def test_stem_cache_round_trip_and_pruning(tmp_path, monkeypatch):
    from countin.core import stemcache

    monkeypatch.setattr(stemcache, "folder", lambda: tmp_path / "cache")
    song = tmp_path / "song.wav"
    song.write_bytes(b"not really audio, only hashed")
    k = stemcache.key(song)
    st = {"guitar": np.full((1000, 2), 0.25, np.float32), "vocals": np.zeros((1000, 2), np.float32)}
    assert stemcache.load(k, tuple(st), 1000) is None
    stemcache.save(k, st, 44100)
    back = stemcache.load(k, tuple(st), 1000)
    assert np.allclose(back["guitar"], 0.25, atol=1e-4) and back["vocals"].shape == (1000, 2)
    assert stemcache.load(k, tuple(st), 999) is None  # another length is another song
    stemcache.prune(limit=0)
    assert stemcache.load(k, tuple(st), 1000) is None


def test_hi_hat_follows_the_music_level_around_a_pause():
    from countin.core import zones as zn

    beats = np.arange(0, 16, 0.5)
    bars = zn.bars_from(beats, beats[::4], 16.0)  # eight bars of 2 s
    quiet_verse = [-12.0, -12.0, -60.0, -60.0, -12.0, -12.0, 0.0, 0.0]
    gains = [c.gain for c in zn.zone_clicks(bars, [zn.Zone(2, 3)], 0.0, quiet_verse)]
    assert gains == pytest.approx([10 ** (-12 / 20)] * 8)  # as soft as the verse around it

    loud = [6.0] * 8
    assert zn.zone_gain(loud, zn.Zone(2, 3)) == pytest.approx(10 ** (3 / 20))  # capped above the count-in level
    assert {c.gain for c in zn.zone_clicks(bars, [zn.Zone(2, 3)], 0.0)} == {1.0}  # without levels: plain


def test_auto_threshold_counts_bleed_as_quiet():
    from countin.core import zones as zn

    sr = 1000
    rng = np.random.default_rng(0)
    # bars of 1 s: silence, then two bars with only guitar bleed left, then the band; one quieter bass bar
    levels = [-65] * 6 + [-28] * 2 + [-4] * 40 + [-13] + [-4] * 20
    x = np.concatenate([rng.standard_normal(sr) * 10 ** (db / 20) for db in levels]).astype(np.float32)[:, None]
    bars = [zn.Bar(float(i), float(i + 1), [float(i), i + 0.5]) for i in range(len(levels))]
    ref = zn.level_db(x)
    th = zn.auto_threshold(x, sr, bars, ref)
    assert th is not None
    quiet = zn.quiet_bars(x, sr, bars, ref, th)
    assert {6, 7} <= quiet and 48 not in quiet  # the bleed bars count as quiet, the bass bar does not

    flat = np.ones((10 * sr, 1), np.float32) * 0.3
    assert zn.auto_threshold(flat, sr, bars[:10], zn.level_db(flat)) is None


def test_pause_build_up_runs_into_the_entry_in_eighths():
    from countin.core import zones as zn

    beats = np.arange(0, 16, 0.5)
    bars = zn.bars_from(beats, beats[::4], 16.0)  # 4/4 bars of 2 s
    plain = zn.zone_clicks(bars, [zn.Zone(2, 3)], 0.0)
    built = zn.zone_clicks(bars, [zn.Zone(2, 3)], 0.0, build_up=True)
    assert [c.time for c in plain] == [4, 4.5, 5, 5.5, 6, 6.5, 7, 7.5]
    # the last bar: quarters on 1 and 2, eighths over 3 and 4, the last three louder
    assert [c.time for c in built][4:] == [6, 6.5, 7, 7.25, 7.5, 7.75]
    assert [c.gain > 1 for c in built][-4:] == [False, True, True, True]


def test_pause_search_keeps_the_lowest_level_each_part_had():
    from countin import gui

    from types import SimpleNamespace

    t = gui.Track(Path("x.wav"))
    s = SimpleNamespace(levels={"guitar": float("-inf")})
    t.search_levels = gui.search_levels(t, s)
    s.levels = {"guitar": -5.0}  # raised again to check the hi-hat against it
    assert gui.search_levels(t, s) == {"guitar": float("-inf")}
    s.levels = {}
    assert gui.search_levels(t, s) == {"guitar": float("-inf")}
    s.levels = {"vocals": -12.0}
    assert gui.search_levels(t, s) == {"guitar": float("-inf"), "vocals": -12.0}


def test_render_slows_the_song_and_keeps_clicks_on_the_slowed_grid():
    sr = 48000
    pcm = np.zeros((sr * 4, 2), np.float32)
    pcm[sr * 2 : sr * 2 + 200] = 0.9  # the "1" at 2 s
    clicks = [rd.Click(1.0, True), rd.Click(1.5, False)]
    r = rd.render(pcm, sr, clicks, 2.0, speed=0.8)
    assert len(r.pcm) == pytest.approx(len(pcm) / 0.8, abs=2)
    assert [s / sr for s in r.click_samples] == pytest.approx([1.25, 1.875])
    hit = np.argmax(np.abs(r.pcm[int(2.3 * sr) : int(2.7 * sr), 0]) > 0.3) / sr + 2.3
    assert hit == pytest.approx(2.0 / 0.8 + r.offset, abs=0.003)  # the song's "1" moved to 2.5 s with them
