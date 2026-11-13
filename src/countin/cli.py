import argparse
import sys
from pathlib import Path

from . import update
from .core import analyze as an
from .core import io, render as rd


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="countin", description="Insert a drum-stick count-in before the first downbeat.")
    p.add_argument("--version", action="version", version=update.current_version())
    p.add_argument("songs", nargs="+", type=Path, help="mp3, FLAC, ALAC or WAV files; several only with --dry-run")
    p.add_argument("--out", type=Path, help="output file; its extension picks the format (.mp3, .flac, .m4a, .wav)")
    p.add_argument("--format", choices=sorted(io.FORMATS),
                   help="output format when --out is not given (default: same as the source, WAV -> flac)")
    p.add_argument("--bars", type=int, default=1, choices=(1, 2))
    p.add_argument("--meter", default="4/4", choices=sorted(rd.METERS))
    p.add_argument("--style", choices=("even", "buildup"), default="even",
                   help="buildup: second half twice as dense, the last three hits louder")
    p.add_argument("--bpm", type=float, help="override detected tempo")
    p.add_argument("--first-beat", type=float, metavar="SEC", help="override the first downbeat")
    tempo = p.add_mutually_exclusive_group()
    tempo.add_argument("--tempo-x2", action="store_true")
    tempo.add_argument("--tempo-half", action="store_true")
    p.add_argument("--pattern", type=Path, metavar="FILE.mid", help="rhythm template; length in whole bars replaces --bars")
    p.add_argument("--pre-roll", choices=("keep", "trim"), default="keep")
    p.add_argument("--guitar-db", type=float, metavar="DB",
                   help="guitar level after separation, e.g. -12; --guitar-db=-inf removes it")
    p.add_argument("--vocals-db", type=float, metavar="DB", help="vocal level, same as --guitar-db")
    p.add_argument("--volume", type=float, default=0.0, metavar="DB", help="count-in gain")
    p.add_argument("--dry-run", action="store_true", help="analyze only")
    return p


def report(path: Path, a: an.Analysis) -> None:
    print(f"== {path.name}")
    print(f"BPM            {a.bpm:.2f}")
    print(f"first downbeat {a.first_downbeat:.3f} s")
    print(f"confidence     {a.confidence:.2f}  (activation {a.activation:.2f}, regularity {a.regularity:.2f}, "
          f"bar phase {a.bar_agreement:.2f})")
    print(f"attack snap    {a.snap_ms:+.1f} ms")
    print(f"beats per bar  {a.beats_per_bar}")
    print("first beats    " + " ".join(f"{b:.3f}" for b in a.beats[:8]))
    print("downbeats      " + " ".join(f"{b:.3f}" for b in a.downbeats[:4]))
    for n in a.notes:
        print(f"note: {n}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if len(args.songs) > 1 and not args.dry_run:
        sys.exit("several files need --dry-run")

    rows = []
    for song in args.songs:
        pcm, info = io.decode(song)
        try:
            a = an.analyze(pcm, info.sr)
        except ValueError as e:
            a = None
            if args.dry_run:
                print(f"{song.name}: {e}", file=sys.stderr)
            elif args.bpm and args.first_beat is not None:
                print(f"warning: detection failed ({e}), using --bpm and --first-beat", file=sys.stderr)
            else:
                sys.exit(f"{song.name}: {e}; set --bpm and --first-beat by hand")
        if a and len(args.songs) == 1:
            report(song, a)
        rows.append((song.name, a))

    if args.dry_run:
        if len(rows) > 1:
            w = max(len(n) for n, _ in rows)
            print(f"{'file':<{w}}  {'BPM':>7}  {'downbeat':>8}  {'conf':>4}  bpb")
            for n, a in filter(lambda r: r[1], rows):
                print(f"{n:<{w}}  {a.bpm:7.2f}  {a.first_downbeat:8.3f}  {a.confidence:4.2f}  {a.beats_per_bar}")
        return 0

    song, a = args.songs[0], rows[0][1]
    if args.bpm:
        period = 60.0 / args.bpm
    else:
        period, note = an.beat_period(a, rd.METERS[args.meter][0])
        if note:
            print(f"note: {note}; use --tempo-x2/--tempo-half or --bpm if it is wrong", file=sys.stderr)
    period *= 0.5 if args.tempo_x2 else 2.0 if args.tempo_half else 1.0
    t0 = args.first_beat if args.first_beat is not None else a.first_downbeat

    grid = rd.buildup_grid if args.style == "buildup" else rd.click_grid
    clicks = (rd.pattern_grid(args.pattern, t0, period, args.meter) if args.pattern
              else grid(t0, period, args.bars, args.meter))
    levels = {k: v for k, v in (("guitar", args.guitar_db), ("vocals", args.vocals_db)) if v is not None and v < 0}
    if levels:
        from .core import stems

        print("separating stems...")
        pcm = stems.attenuate(pcm, stems.separate(pcm, info.sr), levels)
    r = rd.render(pcm, info.sr, clicks, t0, args.pre_roll, args.volume)
    if r.clipped:
        print(f"warning: {r.clipped} samples clipped, lower --volume", file=sys.stderr)

    out = args.out or io.output_path(song, args.format or info.format, levels)
    io.encode(r.pcm, info, out, tags_from=song)

    downbeat_out = t0 + r.offset
    last_click = r.click_samples[-1] / info.sr
    print(f"\nused BPM {60 / period:.2f}, downbeat {t0:.3f} s -> {downbeat_out:.4f} s in output")
    print(f"last click {last_click:.4f} s, {downbeat_out - last_click:.4f} s before the downbeat")
    print(f"saved {out}")
    print(f"check: countin-verify \"{out}\" --last-click {last_click:.5f} --meter {args.meter}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
