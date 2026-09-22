"""orchestra_live - write orchestral music as text, play and export it."""
from .backends import Backend, MidiBackend, PrintBackend, Synth, SynthBackend, auto_backend
from .clock import Clock, TempoMap
from .core import Part, Performance, Piece
from .instruments import INSTRUMENTS, Instrument, get_instrument, register
from .notation import (Event, Measure, ParseError, Parser, PartData, key_accidentals,
                       midi_to_note, note_to_midi, parse_duration, parse_tempo)
