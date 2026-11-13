"""Check a count-in file: where the last click really is, and where the song's downbeat is."""

import argparse
import sys
from pathlib import Path

import numpy as np

from .core import analyze as an
from .core import io, render as rd


def find_onset(mono: np.ndarray, template: np.ndarray, around: int, radius: int) -> int:
    """Sample where `template` starts, searched within around ± radius by normalized cross-correlation."""
    lo = max(0, around - radius)
    hi = min(len(mono) - len(template), around + radius)
    seg = mono[lo : hi + len(template)]
    corr = np.correlate(seg, template, mode="valid")
    energy = np.sqrt(np.convolve(seg**2, np.ones(len(template)), mode="valid")) + 1e-12
    return lo + int(np.argmax(corr / energy))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="countin-verify")
    p.add_argument("file", type=Path)
    p.add_argument("--last-click", type=float, metavar="SEC", help="planned last click, printed by countin")
    p.add_argument("--meter", default="4/4", choices=sorted(rd.METERS))
    args = p.parse_args(argv)

    pcm, info = io.decode(args.file)
    sr = info.sr
    mono = pcm.mean(axis=1)
    hi, lo = rd.load_samples(sr)
    a = an.analyze(pcm, sr)
    step = a.period / rd.METERS[args.meter][1]

    # The detector also sees the clicks as beats, so the song grid starts at the first beat after the last click.
    # Beats, not downbeats: the clicks can shift where the detector puts bar lines.
    guess = args.last_click if args.last_click is not None else a.first_downbeat - step
    after = np.flatnonzero(a.beats > guess + 0.5 * step)
    if not len(after):
        sys.exit("no beat after the count-in")
    k = int(after[0])
    song_period, downbeat_fit, *_ = an.fit_grid(a.beats, k)
    downbeat_fit = an.snap_onset(pcm, sr, downbeat_fit) or downbeat_fit

    click = find_onset(mono, lo, int(round(guess * sr)), int(0.05 * sr)) / sr

    print(f"last click (xcorr)        {click:.5f} s")
    if args.last_click is not None:
        print(f"  vs planned              {(click - args.last_click) * 1000:+.2f} ms   <- codec/delay check, target < 5 ms")
    print(f"song beat 1 (detector)    {downbeat_fit:.5f} s, BPM {60 / song_period:.2f}")
    err = (downbeat_fit - song_period / rd.METERS[args.meter][1]) - click
    print(f"  click vs downbeat-1     {err * 1000:+.2f} ms   <- detector re-run plus attack snap")
    return 0


if __name__ == "__main__":
    sys.exit(main())
