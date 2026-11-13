from pathlib import Path
"""Grid, splice and codec-delay checks on synthetic signals with a known tempo and downbeat."""

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
    for text in (*gui.ICON_CHOICES.values(), *gui.OUT_LEVELS.values(), gui.FORMAT_NAMES[None], i18n.LANGUAGES["system"]):
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
