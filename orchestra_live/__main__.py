"""
python -m orchestra_live piece.orch                    play
python -m orchestra_live piece.orch --tempo 60         play slower (also q.=40)
python -m orchestra_live piece.orch --check            validate only
python -m orchestra_live piece.orch --midi out.mid --wav out.wav [--no-play]
python -m orchestra_live piece.orch --insert-after 6   renumber: make room for a new measure 7
"""
import argparse
import sys

from .backends import MidiBackend, WinmmBackend
from .core import Piece
from .notation import ParseError


def main(argv=None):
    ap = argparse.ArgumentParser(prog="orchestra_live", description="Play, check or export a .orch piece")
    ap.add_argument("file")
    ap.add_argument("--check", action="store_true", help="validate and report, don't play")
    ap.add_argument("--midi", metavar="OUT.mid")
    ap.add_argument("--wav", metavar="OUT.wav", help="render with the built-in synth")
    ap.add_argument("--dry", action="store_true", help="print events instead of playing")
    ap.add_argument("--port", help="MIDI output port (substring match)")
    ap.add_argument("--no-play", action="store_true")
    ap.add_argument("--tempo", metavar="BPM", help="override the tempo, e.g. 80 or q.=60")
    ap.add_argument("--insert-after", type=int, metavar="N",
                    help="renumber the file so a new measure can be inserted after measure N")
    args = ap.parse_args(argv)

    if args.insert_after is not None:
        Piece.renumber(args.file, args.insert_after)
        print(f"renumbered: measures after {args.insert_after} shifted up by 1")
        return
    try:
        piece = Piece.load(args.file)
    except ParseError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if args.tempo:
        piece.set_tempo(args.tempo)
    print(piece)
    if args.midi:
        piece.export_midi(args.midi); print(f"wrote {args.midi}")
    if args.wav:
        piece.export_wav(args.wav); print(f"wrote {args.wav}")
    if args.check or args.no_play:
        return
    backend = "print" if args.dry else None
    if args.port:
        try:
            backend = MidiBackend(args.port)
        except Exception:
            backend = WinmmBackend(args.port)
    piece.play(backend)


if __name__ == "__main__":
    sys.exit(main())
