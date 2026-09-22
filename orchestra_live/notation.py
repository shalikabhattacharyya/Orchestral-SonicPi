"""
Text notation -> timed events, measure by measure.

    1| mf D5:q F5 B5 |  2| C6:h. |  key: G m  time: 2/4  3| sfz D5:q .D5:e .D5:e |
    4-5| |   6| stacc B4:e D5 F5 B5 |  7| ord (F5:e G5 A5 B5) |  8| A5:q >B5:q@ |

Measures        N| ... |   numbered, in order, must add up to the time signature
                0| ... |   pickup (may be short)       5-8| |   multi-measure rest
                &          second voice (divisi): restarts at the top of the measure
Between measures   key: G m    key: Eb    time: 2/4     (apply from the next measure)

Notes           C4  F#3  Bb2  En4   (key signature applies; n = natural)
                :q  duration  w h q e s t, dots q., tuplets e3, or beats :1.5  (sticky)
                [C4 E4 G4]:h chord      r:q rest       o5 default octave
Attach to note  prefix   > accent   ^ marcato   . staccato   _ tenuto        (>.C4:q)
                suffix   ~ tie   @ fermata (everyone holds)   * tremolo      (C4:h~)
                before   sfz sf rfz fp   tr (trill)   {D4} grace note(s)     (sfz C4:q)
Sticky modes    stacc  legato  ord        pizz  arco        con sord  senza sord
Dynamics        ppp pp p mp mf f ff fff    cresc / <   dim / >   (until the next dynamic)
Slur            ( ... )
Tempo line      only in the "Tempo:" part:   96   q.=88   rit   rit:60   accel   a tempo

Every token type is a (regex, handler) pair in Parser.handlers; add your own
with @parser.token(r"...").  Newest registration wins.
"""
from __future__ import annotations

import math
import re
import warnings
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any, Callable, Dict, List, Optional, Tuple


class ParseError(ValueError):
    pass


@dataclass
class Event:
    kind: str                     # note | rest | tempo | tech | hold
    start: float                  # beats from the start of the part
    duration: float
    midi: Optional[int] = None
    velocity: int = 80
    gate: Optional[float] = None  # fraction of duration that sounds (None = default)
    value: Any = None             # tempo: (bpm | ('rit'|'accel', target) | 'atempo'); tech: name
    measure: int = 0

    @property
    def end(self) -> float:
        return self.start + self.duration

    @property
    def is_note(self) -> bool:
        return self.kind == "note"


@dataclass
class Measure:
    number: int
    start: float
    length: float
    time: Tuple[int, int]
    key: str


@dataclass
class PartData:
    measures: List[Measure]
    events: List[Event]

    def length(self) -> float:
        return max((m.start + m.length for m in self.measures), default=0.0)


# ---------------------------------------------------------------- pitch / key
_SEMITONE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_LETTERS = "CDEFGAB"
_ACCIDENTAL = {"#": 1, "##": 2, "b": -1, "bb": -2, "n": 0}
_NOTE_RE = re.compile(r"^([A-G])(##|#|bb|b|n)?(-?\d)?$")
_FLAT_ORDER, _SHARP_ORDER = "BEADGCF", "FCGDAEB"
_MAJOR = {"C": 0, "G": 1, "D": 2, "A": 3, "E": 4, "B": 5, "F#": 6, "C#": 7,
          "F": -1, "Bb": -2, "Eb": -3, "Ab": -4, "Db": -5, "Gb": -6, "Cb": -7}
_MINOR = {"A": 0, "E": 1, "B": 2, "F#": 3, "C#": 4, "G#": 5, "D#": 6, "A#": 7,
          "D": -1, "G": -2, "C": -3, "F": -4, "Bb": -5, "Eb": -6, "Ab": -7}


def normalize_key(text: str) -> str:
    """'G m' -> 'Gm', 'g minor' -> 'Gm', 'Eb major' -> 'Eb', 'Bb' -> 'Bb'."""
    m = re.fullmatch(r"\s*([A-Ga-g])([#b]?)\s*(m|M|min|maj|minor|major)?\s*", text)
    if not m:
        raise ParseError(f"bad key {text!r}")
    tonic = m.group(1).upper() + m.group(2)
    minor = (m.group(3) or "").lower() in ("m", "min", "minor")
    return tonic + ("m" if minor else "")


def key_accidentals(name: str) -> Dict[str, int]:
    """'Bb' -> {'B': -1, 'E': -1};  'Gm' -> same;  'D' -> {'F': 1, 'C': 1}."""
    name = normalize_key(name)
    minor = name.endswith("m")
    tonic = name[:-1] if minor else name
    table = _MINOR if minor else _MAJOR
    if tonic not in table:
        raise ParseError(f"unknown key {name!r}")
    n = table[tonic]
    return ({l: 1 for l in _SHARP_ORDER[:n]} if n >= 0
            else {l: -1 for l in _FLAT_ORDER[:-n]})


def note_to_midi(name: str, default_octave: int = 4, key: Optional[Dict[str, int]] = None) -> int:
    m = _NOTE_RE.match(name)
    if not m:
        raise ParseError(f"bad note name {name!r}")
    letter, acc, octave = m.groups()
    octave = int(octave) if octave is not None else default_octave
    shift = (key or {}).get(letter, 0) if acc is None else _ACCIDENTAL[acc]
    return (octave + 1) * 12 + _SEMITONE[letter] + shift


def upper_neighbour(name: str, default_octave: int, key: Optional[Dict[str, int]]) -> int:
    """Diatonic note above *name* in the current key (for trills)."""
    m = _NOTE_RE.match(name)
    letter, _, octave = m.groups()
    octave = int(octave) if octave is not None else default_octave
    i = _LETTERS.index(letter)
    nxt = _LETTERS[(i + 1) % 7]
    return note_to_midi(f"{nxt}{octave + (1 if letter == 'B' else 0)}", default_octave, key)


def midi_to_note(midi: int) -> str:
    names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    return f"{names[midi % 12]}{midi // 12 - 1}"


# ---------------------------------------------------------------- duration / tempo
_DURATION_BASE = {k: Fraction(v) for k, v in
                  dict(w=4, h=2, q=1, e="1/2", s="1/4", t="1/8").items()}
_DUR_RE = re.compile(r"^([whqest])(\.*)(\d+)?$")


def parse_duration(text: str) -> Fraction:
    """'q' -> 1, 'q.' -> 3/2, 'e3' -> 1/3, '1.5' -> 3/2  (quarter-note beats, exact)."""
    try:
        return Fraction(text)
    except ValueError:
        pass
    m = _DUR_RE.match(text)
    if not m:
        raise ParseError(f"bad duration {text!r}")
    base, dots, tuplet = m.groups()
    value = _DURATION_BASE[base] * (2 - Fraction(1, 2 ** len(dots)))
    if tuplet:
        n = int(tuplet)
        value *= Fraction(2 ** int(math.log2(n)), n)
    return value


def parse_tempo(text: str) -> float:
    """'96' -> 96 (quarter = 96);  'q.=88' -> 132;  'h=60' -> 120.  Returns quarter BPM."""
    m = re.fullmatch(r"(?:([whqe]\.?)=)?(\d+(?:\.\d+)?)", text.strip())
    if not m:
        raise ParseError(f"bad tempo {text!r}")
    unit = float(parse_duration(m.group(1))) if m.group(1) else 1.0
    return float(m.group(2)) * unit


DYNAMICS = {"ppp": 20, "pp": 33, "p": 49, "mp": 64, "mf": 80, "f": 96, "ff": 112, "fff": 126}
ACCENTS = {"sfz": 120, "sf": 110, "rfz": 115, "fp": 100}
GATE_NORMAL, GATE_SLUR, GATE_STACCATO, GATE_MARCATO = 0.9, 1.0, 0.5, 0.7
ACCENT_BOOST = 20
ORNAMENT_STEP = 0.125            # beats: trill / tremolo / grace-note speed
FERMATA_FACTOR = 1.6


# ---------------------------------------------------------------- tokenizer
_PRE = [
    (re.compile(r"key:\s*([A-Ga-g][#b]?)(?:[ \t]*(m|M|min|maj|minor|major)\b)?"),
          lambda m: "key=" + normalize_key(m.group(1) + " " + (m.group(2) or ""))),
    (re.compile(r"time:\s*(\d+)\s*/\s*(\d+)"), r"time=\1/\2"),
    (re.compile(r"\bcon\s+sord\b"), "consord"),
    (re.compile(r"\bsenza\s+sord\b"), "senzasord"),
    (re.compile(r"\ba\s+tempo\b"), "atempo"),
]
_TOKEN_RE = re.compile(r"\d+(?:-\d+)?\||\||\{[^}]*\}|\[[^\]]*\][^\s|(){}]*|\(|\)|&|[^\s()|{}]+")


def tokenize(text: str) -> List[str]:
    text = re.sub(r"(?:^|(?<=\s))#.*?$", "", text, flags=re.M)   # F#3 is not a comment
    for pat, rep in _PRE:
        text = pat.sub(rep, text)
    return _TOKEN_RE.findall(text)


# ---------------------------------------------------------------- parser
@dataclass
class ParseState:
    cursor: Fraction = Fraction(0)
    duration: Fraction = Fraction(1)
    velocity: int = 80
    octave: int = 4
    key_name: str = "C"
    key: Dict[str, int] = field(default_factory=dict)
    time: Tuple[int, int] = (4, 4)
    mode_gate: Optional[float] = None       # stacc / legato / ord
    in_slur: bool = False
    slur_events: List[Event] = field(default_factory=list)
    pending_ties: Dict[int, Event] = field(default_factory=dict)
    hairpin: Optional[tuple] = None
    next_note: Dict[str, Any] = field(default_factory=dict)   # accent / trill / grace
    # measures
    measure: Optional[Measure] = None
    measure_end: Optional[int] = None       # for N-M| ranges
    voice: int = 1
    expected: Optional[int] = None
    measures: List[Measure] = field(default_factory=list)
    events: List[Event] = field(default_factory=list)

    @property
    def beats_per_bar(self) -> Fraction:
        return Fraction(self.time[0] * 4, self.time[1])


Handler = Callable[[re.Match, ParseState, "Parser"], None]
_PITCH = r"[A-G](?:##|#|bb|b|n)?(?:-?\d)?"


class Parser:
    def __init__(self, time: Tuple[int, int] = (4, 4), key: Optional[str] = None,
                 default_octave: int = 4, tempo_line: bool = False,
                 measure_lengths: Optional[Dict[int, float]] = None):
        self.time, self.key, self.default_octave = tuple(time), key, default_octave
        self.tempo_line = tempo_line                # relaxed checking for the Tempo: part
        self.measure_lengths = measure_lengths or {}
        self.handlers: List[tuple] = []
        self._register_defaults()

    def token(self, pattern: str):
        def deco(fn: Handler) -> Handler:
            self.handlers.insert(0, (re.compile(pattern), fn))
            return fn
        return deco

    # ---- entry --------------------------------------------------------
    def parse(self, text: str) -> PartData:
        st = ParseState(octave=self.default_octave, time=self.time)
        if self.key:
            st.key_name, st.key = normalize_key(self.key), key_accidentals(self.key)
        for tok in tokenize(text):
            for pattern, handler in self.handlers:
                m = pattern.fullmatch(tok)
                if m:
                    handler(m, st, self)
                    break
            else:
                raise ParseError(self._where(st, f"unknown token {tok!r}"))
        if st.measure is not None:
            raise ParseError(f"measure {st.measure.number} is never closed (missing '|')")
        if st.in_slur:
            warnings.warn("unclosed slur", stacklevel=3)
        return PartData(st.measures, st.events)

    @staticmethod
    def _where(st: ParseState, msg: str) -> str:
        return f"measure {st.measure.number}: {msg}" if st.measure else msg

    # ---- helpers for handlers ----------------------------------------
    def in_measure(self, st: ParseState, what: str) -> Measure:
        if st.measure is None:
            raise ParseError(f"{what!r} must be inside a measure (after 'N|')")
        return st.measure

    def take_duration(self, suffix: Optional[str], st: ParseState) -> Fraction:
        if suffix:
            st.duration = parse_duration(suffix)
        return st.duration

    def add(self, st: ParseState, ev: Event) -> Event:
        ev.measure = st.measure.number if st.measure else 0
        st.events.append(ev)
        return ev

    def emit_notes(self, names: List[str], suffix: Optional[str], prefix: str,
                   flags: str, st: ParseState) -> None:
        """Shared by note and chord handlers."""
        self.in_measure(st, names[0])
        d = self.take_duration(suffix, st)
        pend, st.next_note = st.next_note, {}
        vel = st.velocity
        if ">" in prefix or "^" in prefix:
            vel += ACCENT_BOOST
        if "accent" in pend:
            vel = ACCENTS[pend["accent"]]
        vel = min(127, vel)
        if "." in prefix:
            gate = GATE_STACCATO
        elif "^" in prefix:
            gate = GATE_MARCATO
        elif "_" in prefix or st.in_slur:
            gate = GATE_SLUR
        else:
            gate = st.mode_gate
        start = st.cursor
        midis = [note_to_midi(n, st.octave, st.key) for n in names]

        # grace notes steal from the start of the main note
        graces = pend.get("grace", [])
        if graces:
            step = min(Fraction(ORNAMENT_STEP), d / (2 * len(graces)))
            for g in graces:
                self.add(st, Event("note", float(start), float(step), g, vel, GATE_SLUR))
                start += step
            d_main = d - step * len(graces)
        else:
            d_main = d

        if "tr" in pend or "*" in flags:                     # trill / tremolo
            alt = [upper_neighbour(names[0], st.octave, st.key)] if "tr" in pend else []
            seq = (midis + [alt[0]]) if alt else midis
            pos, i = start, 0
            end = start + d_main
            while pos < end:
                step = min(Fraction(ORNAMENT_STEP), end - pos)
                for mid in ([seq[i % len(seq)]] if alt else midis):
                    self.add(st, Event("note", float(pos), float(step), mid, vel, GATE_SLUR))
                pos += step; i += 1
        else:
            new_ties: Dict[int, Event] = {}
            for midi in midis:
                prev = st.pending_ties.pop(midi, None)
                if prev is not None:
                    prev.duration += float(d_main)
                    ev = prev
                else:
                    ev = self.add(st, Event("note", float(start), float(d_main), midi, vel, gate,
                                            value=pend.get("accent")))
                    if st.in_slur:
                        st.slur_events.append(ev)
                if "~" in flags:
                    new_ties[midi] = ev
            if st.pending_ties:
                warnings.warn(self._where(st, "tie into a different pitch"), stacklevel=5)
            st.pending_ties = new_ties

        if "@" in flags:
            self.add(st, Event("hold", float(st.cursor), float(d), value=FERMATA_FACTOR))
        if pend.get("accent") == "fp":
            st.velocity = DYNAMICS["p"]
        st.cursor += d

    def set_dynamic(self, vel: int, st: ParseState) -> None:
        if st.hairpin is not None:
            first, v0 = st.hairpin
            span = [e for e in st.events[first:] if e.is_note and e.value is None]
            if span:
                t0, t1 = span[0].start, float(st.cursor)
                for e in span:
                    frac = (e.start - t0) / (t1 - t0) if t1 > t0 else 1.0
                    e.velocity = int(round(v0 + (vel - v0) * frac))
            st.hairpin = None
        st.velocity = vel

    # ---- measures -----------------------------------------------------
    def open_measure(self, first: int, last: Optional[int], st: ParseState) -> None:
        if st.measure is not None:
            raise ParseError(f"measure {st.measure.number} not closed before measure {first}")
        if self.tempo_line:                                   # may skip measures
            if st.expected is not None and first < st.expected:
                raise ParseError(f"measure {first} comes after measure {st.expected - 1}")
        elif st.expected is not None and first != st.expected:
            raise ParseError(f"expected measure {st.expected}, found {first}")
        elif st.expected is None and first not in (0, 1):
            raise ParseError(f"first measure must be 0 (pickup) or 1, found {first}")
        length = self.measure_lengths.get(first, float(st.beats_per_bar))
        st.measure = Measure(first, float(st.cursor), length, st.time, st.key_name)
        st.measure_end = last
        st.voice = 1

    def close_measure(self, st: ParseState) -> None:
        m = self.in_measure(st, "|")
        if st.measure_end is not None:                        # N-M| | multi-measure rest
            if st.cursor != Fraction(m.start):
                raise ParseError(f"measures {m.number}-{st.measure_end} must be empty (multi-measure rest)")
            for n in range(m.number, st.measure_end + 1):
                length = self.measure_lengths.get(n, float(st.beats_per_bar))
                st.measures.append(Measure(n, float(st.cursor), length, st.time, st.key_name))
                st.cursor += Fraction(length)
            st.expected = st.measure_end + 1
        else:
            got = st.cursor - Fraction(m.start)
            if got == 0 and not self.tempo_line:              # N| |  = whole-bar rest
                self.add(st, Event("rest", m.start, m.length))
                got = Fraction(m.length)
            if self.tempo_line:
                pass
            elif m.number == 0:
                if got > m.length:
                    raise ParseError(f"pickup measure has {float(got):g} beats, "
                                     f"more than a {st.time[0]}/{st.time[1]} bar")
                m.length = float(got)
            elif got != Fraction(m.length):
                raise ParseError(f"measure {m.number} has {float(got):g} beats, expected "
                                 f"{m.length:g} ({st.time[0]}/{st.time[1]})")
            st.measures.append(m)
            st.cursor = Fraction(m.start) + Fraction(m.length)
            st.expected = m.number + 1
        st.measure = st.measure_end = None

    # ---- default token set -------------------------------------------
    def _register_defaults(self) -> None:
        @self.token(r"(\d+)(?:-(\d+))?\|")
        def open_bar(m, st, p):
            p.open_measure(int(m.group(1)), int(m.group(2)) if m.group(2) else None, st)

        @self.token(r"\|")
        def close_bar(m, st, p):
            p.close_measure(st)

        @self.token(r"&")
        def divisi(m, st, p):
            mm = p.in_measure(st, "&")
            got = st.cursor - Fraction(mm.start)
            if got != Fraction(mm.length):
                raise ParseError(f"measure {mm.number} voice {st.voice} has {float(got):g} beats "
                                 f"before '&', expected {mm.length:g}")
            st.cursor, st.voice = Fraction(mm.start), st.voice + 1
            st.pending_ties = {}

        @self.token(r"key=(\S+)")
        def key(m, st, p):
            if st.measure is not None:
                raise ParseError(p._where(st, "key changes go between measures"))
            st.key_name, st.key = m.group(1), key_accidentals(m.group(1))

        @self.token(r"time=(\d+)/(\d+)")
        def time_sig(m, st, p):
            if st.measure is not None:
                raise ParseError(p._where(st, "time changes go between measures"))
            st.time = (int(m.group(1)), int(m.group(2)))

        @self.token(r"(ppp|pp|p|mp|mf|f|ff|fff)")
        def dynamic(m, st, p):
            p.set_dynamic(DYNAMICS[m.group(1)], st)

        @self.token(r"cresc|<|dim|decresc|>")
        def hairpin(m, st, p):
            st.hairpin = (len(st.events), st.velocity)

        @self.token(r"sfz|sf|rfz|fp")
        def accent_word(m, st, p):
            st.next_note["accent"] = m.group(0)

        @self.token(r"tr")
        def trill(m, st, p):
            st.next_note["tr"] = True

        @self.token(r"\{([^}]*)\}")
        def grace(m, st, p):
            st.next_note["grace"] = [note_to_midi(n, st.octave, st.key) for n in m.group(1).split()]

        @self.token(r"stacc|legato|ord")
        def mode(m, st, p):
            st.mode_gate = {"stacc": GATE_STACCATO, "legato": GATE_SLUR, "ord": None}[m.group(0)]

        @self.token(r"pizz|arco|consord|senzasord")
        def technique(m, st, p):
            p.in_measure(st, m.group(0))
            p.add(st, Event("tech", float(st.cursor), 0.0, value=m.group(0)))

        @self.token(r"o(-?\d)")
        def octave(m, st, p):
            st.octave = int(m.group(1))

        @self.token(r"\(")
        def slur_open(m, st, p):
            st.in_slur, st.slur_events = True, []

        @self.token(r"\)")
        def slur_close(m, st, p):
            if st.slur_events and st.slur_events[-1].gate == GATE_SLUR:
                st.slur_events[-1].gate = st.mode_gate
            st.in_slur, st.slur_events = False, []

        @self.token(r"r(?::(\S+))?")
        def rest(m, st, p):
            p.in_measure(st, "r")
            d = p.take_duration(m.group(1), st)
            p.add(st, Event("rest", float(st.cursor), float(d)))
            st.cursor += d

        @self.token(r"([>^._]*)\[([^\]]*)\](?::([^~@*]+))?([~@*]*)")
        def chord(m, st, p):
            p.emit_notes(m.group(2).split(), m.group(3), m.group(1), m.group(4), st)

        @self.token(rf"([>^._]*)({_PITCH})(?::([^~@*]+))?([~@*]*)")
        def note(m, st, p):
            p.emit_notes([m.group(2)], m.group(3), m.group(1), m.group(4), st)

        # ---- Tempo line only ----
        @self.token(r"(?:[whqe]\.?=)?\d+(?:\.\d+)?")
        def tempo_value(m, st, p):
            if not p.tempo_line:
                raise ParseError(p._where(st, f"{m.group(0)!r}: tempo marks go on the Tempo: line"))
            p.in_measure(st, m.group(0))
            p.add(st, Event("tempo", float(st.cursor), 0.0, value=parse_tempo(m.group(0))))

        @self.token(r"(rit|accel)(?::(\S+))?|atempo")
        def tempo_change(m, st, p):
            if not p.tempo_line:
                raise ParseError(p._where(st, f"{m.group(0)!r}: tempo marks go on the Tempo: line"))
            p.in_measure(st, m.group(0))
            if m.group(0) == "atempo":
                value = "atempo"
            else:
                value = (m.group(1), parse_tempo(m.group(2)) if m.group(2) else None)
            p.add(st, Event("tempo", float(st.cursor), 0.0, value=value))
