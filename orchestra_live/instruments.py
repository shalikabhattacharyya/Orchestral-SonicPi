"""
Orchestral instrument table.

program  General MIDI program number (0-based) for MIDI output
low/high sounding range in MIDI numbers (used to warn about unplayable notes)
family   timbre family used by the built-in synth
"""
from __future__ import annotations

from dataclasses import dataclass

from .notation import note_to_midi as _n


@dataclass(frozen=True)
class Instrument:
    name: str
    program: int
    low: int
    high: int
    family: str      # strings | woodwind | brass | percussion | keyboard | pluck
    pizz_program: int | None = None    # GM program used after "pizz"
    mute_program: int | None = None    # GM program used after "con sord"

    def in_range(self, midi: int) -> bool:
        return self.low <= midi <= self.high


_TABLE = [
    Instrument("Violin",       40, _n("G3"),  _n("A7"),  "strings", pizz_program=45),
    Instrument("Viola",        41, _n("C3"),  _n("E6"),  "strings", pizz_program=45),
    Instrument("Cello",        42, _n("C2"),  _n("C6"),  "strings", pizz_program=45),
    Instrument("Contrabass",   43, _n("E1"),  _n("G4"),  "strings", pizz_program=45),
    Instrument("Harp",         46, _n("C1"),  _n("G7"),  "pluck"),
    Instrument("Piccolo",      72, _n("D5"),  _n("C8"),  "woodwind"),
    Instrument("Flute",        73, _n("C4"),  _n("D7"),  "woodwind"),
    Instrument("Oboe",         68, _n("Bb3"), _n("A6"),  "woodwind"),
    Instrument("English Horn", 69, _n("E3"),  _n("A5"),  "woodwind"),
    Instrument("Clarinet",     71, _n("D3"),  _n("Bb6"), "woodwind"),
    Instrument("Bassoon",      70, _n("Bb1"), _n("Eb5"), "woodwind"),
    Instrument("Horn",         60, _n("B1"),  _n("F5"),  "brass"),
    Instrument("Trumpet",      56, _n("F#3"), _n("D6"),  "brass", mute_program=59),
    Instrument("Trombone",     57, _n("E2"),  _n("F5"),  "brass"),
    Instrument("Tuba",         58, _n("D1"),  _n("F4"),  "brass"),
    Instrument("Timpani",      47, _n("D2"),  _n("C4"),  "percussion"),
    Instrument("Glockenspiel",  9, _n("G5"),  _n("C8"),  "percussion"),
    Instrument("Piano",         0, _n("A0"),  _n("C8"),  "keyboard"),
    Instrument("Celesta",       8, _n("C4"),  _n("C8"),  "keyboard"),
]

INSTRUMENTS = {i.name.lower(): i for i in _TABLE}
INSTRUMENTS.update({
    "violins": INSTRUMENTS["violin"], "violas": INSTRUMENTS["viola"],
    "celli": INSTRUMENTS["cello"], "cellos": INSTRUMENTS["cello"],
    "bass": INSTRUMENTS["contrabass"], "double bass": INSTRUMENTS["contrabass"],
    "french horn": INSTRUMENTS["horn"], "horns": INSTRUMENTS["horn"],
})


def get_instrument(name: str) -> Instrument:
    try:
        return INSTRUMENTS[name.strip().lower()]
    except KeyError:
        raise KeyError(f"unknown instrument {name!r}; known: "
                       + ", ".join(i.name for i in _TABLE)) from None


def register(instrument: Instrument, *aliases: str) -> None:
    """Add a custom instrument to the registry."""
    INSTRUMENTS[instrument.name.lower()] = instrument
    for a in aliases:
        INSTRUMENTS[a.lower()] = instrument
