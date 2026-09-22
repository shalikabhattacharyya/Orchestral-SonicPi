"""
Piece: an orchestral score written in text, played once through in form order.

.orch file
----------
    title  Evening Sketch
    tempo  q.=88                 # or 96 (quarter = 96), h=60 ...
    time   3/8
    key    G m                   # "G", "G m", "Bb", "Eb major" ...
    form   1-8 1-8 9-16          # play order (default: 1 to the end, once)

    Tempo:                       # optional; only place tempo marks are allowed
      9| 80 |  15| rit |  16| a tempo |

    Cello:                       # "Name:" or "Name (Instrument):"
      1| mp B2:h. |  2| F2:h. |  key: G m  time: 2/4  3| D3:h |  4-5| |

Python
------
    piece = Piece.load("sketch.orch");  piece.play();  piece.export_midi("x.mid")
"""
from __future__ import annotations

import re
import threading
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .backends import Backend, PrintBackend, Synth, auto_backend
from .clock import Clock, TempoMap
from .instruments import Instrument, get_instrument
from .notation import (GATE_NORMAL, Event, ParseError, Parser, PartData, midi_to_note,
                       normalize_key, parse_tempo)

RAMP_STEP = 0.25          # beats between sampled tempo points during rit / accel
RIT_FACTOR, ACCEL_FACTOR = 0.6, 1.4
TEMPO_PART = "tempo"


@dataclass
class Part:
    name: str
    instrument: Instrument
    text: str
    data: PartData

    @property
    def measures(self):
        return self.data.measures

    @property
    def events(self):
        return self.data.events


# (perf beat, order, op, midi, value)   order: program change < note off < note on
Item = Tuple[float, int, str, Optional[int], int]


@dataclass
class Performance:
    timelines: Dict[str, List[Item]]
    tempo_points: List[Tuple[float, float]]
    length: float

    def tempo_map(self, initial_bpm: float) -> TempoMap:
        return TempoMap(initial_bpm, self.tempo_points)


class Piece:
    def __init__(self, bpm: float | str = 120, time: Tuple[int, int] = (4, 4),
                 key: Optional[str] = None, title: Optional[str] = None,
                 form: Optional[str] = None, default_octave: int = 4):
        self.bpm = parse_tempo(str(bpm))
        self.time, self.title = tuple(time), title
        self.key = normalize_key(key) if key else None
        self.form_text = form
        self.default_octave = default_octave
        self.parts: Dict[str, Part] = {}
        self.tempo_text: Optional[str] = None
        self._tempo_data: Optional[PartData] = None

    # ---- building -----------------------------------------------------
    def add(self, instrument: str, text: str, name: Optional[str] = None) -> Part:
        if (name or instrument).strip().lower() == TEMPO_PART:
            self.tempo_text = text.strip()
            self._tempo_data = None
            return None
        inst = get_instrument(instrument)
        name = name or inst.name
        try:
            data = Parser(self.time, self.key, self.default_octave).parse(text)
        except ParseError as e:
            raise ParseError(f"{name}: {e}") from None
        bad = sorted({e.midi for e in data.events if e.is_note and not inst.in_range(e.midi)})
        if bad:
            warnings.warn(f"{name}: out of range for {inst.name}: "
                          + " ".join(midi_to_note(m) for m in bad), stacklevel=3)
        part = Part(name, inst, text.strip(), data)
        self.parts[name] = part
        return part

    def __setitem__(self, name: str, text: str) -> None:
        self.add(name, text, name=name)

    def __delitem__(self, name: str) -> None:
        del self.parts[name]

    def set_tempo(self, tempo: float | str) -> None:
        """Change the base tempo ('80', 'q.=60'); Tempo-line marks scale with it."""
        new = parse_tempo(str(tempo))
        ratio = new / self.bpm
        self.bpm = new
        if self._tempo_data is None and self.tempo_text is not None:
            self.validate()
        if self._tempo_data is not None:
            for e in self._tempo_data.events:
                if e.kind != "tempo":
                    continue
                if isinstance(e.value, float):
                    e.value *= ratio
                elif isinstance(e.value, tuple) and e.value[1] is not None:
                    e.value = (e.value[0], e.value[1] * ratio)

    # ---- validation ---------------------------------------------------
    def validate(self) -> Dict[int, float]:
        """Check every part has the same measures; return {number: length}."""
        if not self.parts:
            raise ParseError("piece has no parts")
        ref_name, ref = next(iter(self.parts.items()))
        lengths = {m.number: m.length for m in ref.measures}
        keys = {m.number: m.key for m in ref.measures}
        for name, part in self.parts.items():
            mine = {m.number: m.length for m in part.measures}
            missing = sorted(set(lengths) - set(mine))
            extra = sorted(set(mine) - set(lengths))
            if missing:
                raise ParseError(f"{name} is missing measure(s) {_ranges(missing)} (present in {ref_name})")
            if extra:
                raise ParseError(f"{name} has measure(s) {_ranges(extra)} that {ref_name} does not")
            for n, length in mine.items():
                if length != lengths[n]:
                    raise ParseError(f"measure {n}: {name} is {length:g} beats but "
                                     f"{ref_name} is {lengths[n]:g} (time signature change missing?)")
            for m in part.measures:
                if m.key != keys[m.number]:
                    warnings.warn(f"measure {m.number}: {name} is in {m.key} but {ref_name} "
                                  f"is in {keys[m.number]}", stacklevel=3)
        if self.tempo_text is not None and self._tempo_data is None:
            try:
                self._tempo_data = Parser(self.time, None, tempo_line=True,
                                          measure_lengths=lengths).parse(self.tempo_text)
            except ParseError as e:
                raise ParseError(f"Tempo: {e}") from None
            unknown = sorted({m.number for m in self._tempo_data.measures} - set(lengths))
            if unknown:
                raise ParseError(f"Tempo: measure(s) {_ranges(unknown)} do not exist in the parts")
        return lengths

    def form(self) -> List[int]:
        """Measure numbers in performance order."""
        numbers = sorted(self.validate())
        if not self.form_text:
            return numbers
        order: List[int] = []
        for tok in self.form_text.replace(",", " ").split():
            m = re.fullmatch(r"(\d+)(?:-(\d+))?", tok)
            if not m:
                raise ParseError(f"form: bad token {tok!r}")
            a, b = int(m.group(1)), int(m.group(2) or m.group(1))
            for n in range(a, b + 1):
                if n not in numbers:
                    raise ParseError(f"form: measure {n} does not exist")
                order.append(n)
        return order

    # ---- performance --------------------------------------------------
    def perform(self) -> Performance:
        lengths = self.validate()
        order = self.form()
        perf_start: List[Tuple[int, float]] = []          # (measure number, perf beat)
        beat = 0.0
        for n in order:
            perf_start.append((n, beat))
            beat += lengths[n]
        total = beat

        def place(data: PartData) -> List[Tuple[float, Event]]:
            m_start = {m.number: m.start for m in data.measures}
            by_measure: Dict[int, List[Event]] = {}
            for e in data.events:
                by_measure.setdefault(e.measure, []).append(e)
            out = []
            for n, pb in perf_start:
                for e in by_measure.get(n, []):
                    out.append((pb + (e.start - m_start[n]), e))
            return out

        timelines: Dict[str, List[Item]] = {}
        holds: List[Tuple[float, float, float]] = []
        for name, part in self.parts.items():
            items: List[Item] = []
            for pb, e in place(part.data):
                if e.kind == "note":
                    gate = e.gate if e.gate is not None else GATE_NORMAL
                    items.append((pb, 2, "on", e.midi, e.velocity))
                    items.append((pb + e.duration * gate, 1, "off", e.midi, 0))
                elif e.kind == "tech":
                    prog = {"pizz": part.instrument.pizz_program,
                            "arco": part.instrument.program,
                            "consord": part.instrument.mute_program,
                            "senzasord": part.instrument.program}[e.value]
                    if prog is not None:
                        items.append((pb, 0, "prog", None, prog))
                elif e.kind == "hold":
                    holds.append((pb, pb + e.duration, e.value))
            items.sort(key=lambda i: (i[0], i[1]))
            timelines[name] = items

        tempo_events = place(self._tempo_data) if self._tempo_data else []
        points = _tempo_points(self.bpm, sorted(tempo_events, key=lambda t: t[0]), total)
        points = _apply_holds(points, self.bpm, holds)
        return Performance(timelines, points, total)

    def duration_seconds(self) -> float:
        perf = self.perform()
        return perf.tempo_map(self.bpm).seconds_at(perf.length)

    # ---- playback -----------------------------------------------------
    def play(self, backend: Backend | str | None = None) -> None:
        perf = self.perform()
        clock = Clock(self.bpm)
        if backend is None:
            backend = auto_backend(clock)
        elif backend == "print":
            backend = PrintBackend(clock)
        channels = _channels(len(self.parts))

        def run_part(part: Part, ch: int, items: List[Item]) -> None:
            backend.program_change(ch, part.instrument.program, part.instrument.family)
            sounding: set[int] = set()
            for beat, _, op, midi, val in items:
                if not clock.wait_until(beat):
                    break
                if op == "on":
                    backend.note_on(ch, midi, val); sounding.add(midi)
                elif op == "off":
                    backend.note_off(ch, midi); sounding.discard(midi)
                else:
                    backend.program_change(ch, val, "pluck" if val == 45 else part.instrument.family)
            for midi in sounding:
                backend.note_off(ch, midi)

        def conduct() -> None:
            for beat, bpm in perf.tempo_points:
                if not clock.wait_until(beat):
                    return
                clock.bpm = bpm

        threads = [threading.Thread(target=run_part, daemon=True, args=(p, ch, perf.timelines[n]))
                   for (n, p), ch in zip(self.parts.items(), channels)]
        threads.append(threading.Thread(target=conduct, daemon=True))
        clock.start()
        for t in threads:
            t.start()
        try:
            for t in threads:
                t.join()
        except KeyboardInterrupt:
            print("\nstopped")
        finally:
            clock.stop()
            backend.all_notes_off()
            backend.close()

    # ---- save / load --------------------------------------------------
    def save(self, path: str | Path) -> None:
        lines = []
        if self.title:
            lines.append(f"title  {self.title}")
        lines += [f"tempo  {self.bpm:g}", f"time   {self.time[0]}/{self.time[1]}"]
        if self.key:
            lines.append(f"key    {self.key}")
        if self.form_text:
            lines.append(f"form   {self.form_text}")
        blocks = ([("Tempo", self.tempo_text)] if self.tempo_text else []) + [
            (p.name if p.name.lower() == p.instrument.name.lower()
             else f"{p.name} ({p.instrument.name})", p.text) for p in self.parts.values()]
        for head, text in blocks:
            lines += ["", f"{head}:"] + ["  " + ln.strip() for ln in text.splitlines() if ln.strip()]
        Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")

    _PART_HEAD = re.compile(r"^(?P<name>[^:#(|]+?)\s*(?:\((?P<inst>[^)]+)\))?\s*:\s*$")

    @classmethod
    def load(cls, path: str | Path, **kwargs) -> "Piece":
        header: Dict[str, str] = {}
        parts: List[Tuple[str, str, List[str]]] = []
        for raw in Path(path).read_text(encoding="utf-8").splitlines():
            line = re.sub(r"(?:^|(?<=\s))#.*$", "", raw).rstrip()
            if not line.strip():
                continue
            m = cls._PART_HEAD.match(line)
            if m and not line[0].isspace():
                parts.append((m["name"].strip(), (m["inst"] or m["name"]).strip(), []))
            elif parts:
                parts[-1][2].append(line.strip())
            else:
                k, _, v = line.strip().partition(" ")
                header[k.lower()] = v.strip()
        num, _, den = header.get("time", "4/4").partition("/")
        piece = cls(bpm=header.get("tempo", 120), time=(int(num), int(den)),
                    key=header.get("key") or None, title=header.get("title") or None,
                    form=header.get("form") or None, **kwargs)
        for name, inst, lines in parts:
            piece.add(inst, "\n".join(lines), name=name)
        piece.form()                       # validates parts, Tempo line and form
        return piece

    @staticmethod
    def renumber(path: str | Path, insert_after: int, count: int = 1) -> None:
        """Shift every measure number > insert_after up by *count*, in all parts and the form."""
        text = Path(path).read_text(encoding="utf-8")

        def shift(n: int) -> int:
            return n + count if n > insert_after else n

        text = re.sub(r"(?m)(?:(?<=\s)|^)(\d+)(?:-(\d+))?\|",
                      lambda m: f"{shift(int(m.group(1)))}"
                                + (f"-{shift(int(m.group(2)))}" if m.group(2) else "") + "|", text)
        text = re.sub(r"(?m)^(form\s+)(.*)$",
                      lambda m: m.group(1) + re.sub(r"\d+", lambda d: str(shift(int(d.group(0)))), m.group(2)),
                      text)
        Path(path).write_text(text, encoding="utf-8")

    # ---- export -------------------------------------------------------
    def export_midi(self, path: str | Path, ticks_per_beat: int = 480) -> None:
        import mido
        perf = self.perform()
        mid = mido.MidiFile(ticks_per_beat=ticks_per_beat)
        tick = lambda b: int(round(b * ticks_per_beat))
        meta = mido.MidiTrack(); mid.tracks.append(meta)
        if self.title:
            meta.append(mido.MetaMessage("track_name", name=self.title, time=0))
        meta.append(mido.MetaMessage("time_signature", numerator=self.time[0],
                                     denominator=self.time[1], time=0))
        if self.key:
            try:
                meta.append(mido.MetaMessage("key_signature", key=self.key, time=0))
            except ValueError:
                pass
        last = 0
        for beat, bpm in perf.tempo_map(self.bpm).marks:
            meta.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm), time=tick(beat) - last))
            last = tick(beat)
        for (name, part), ch in zip(self.parts.items(), _channels(len(self.parts))):
            tr = mido.MidiTrack(); mid.tracks.append(tr)
            tr.append(mido.MetaMessage("track_name", name=name, time=0))
            tr.append(mido.Message("program_change", channel=ch, program=part.instrument.program, time=0))
            last = 0
            for beat, _, op, midi, val in perf.timelines[name]:
                t = tick(beat)
                if op == "prog":
                    tr.append(mido.Message("program_change", channel=ch, program=val, time=t - last))
                else:
                    tr.append(mido.Message("note_on" if op == "on" else "note_off", channel=ch,
                                           note=midi, velocity=val, time=t - last))
                last = t
        mid.save(str(path))

    def export_wav(self, path: str | Path, samplerate: int = 44100, block: int = 1024,
                   tail_seconds: float = 1.5) -> None:
        """Render offline through the built-in synth (needs numpy only)."""
        import wave
        perf = self.perform()
        tmap = perf.tempo_map(self.bpm)
        synth = Synth(samplerate)
        schedule = []
        for (name, part), ch in zip(self.parts.items(), _channels(len(self.parts))):
            synth.program_change(ch, part.instrument.program, part.instrument.family)
            for beat, order, op, midi, val in perf.timelines[name]:
                schedule.append((int(tmap.seconds_at(beat) * samplerate), order, op, ch, midi, val, part))
        schedule.sort(key=lambda s: (s[0], s[1]))
        total = (schedule[-1][0] if schedule else 0) + int(tail_seconds * samplerate)
        np, chunks, frame, i = synth.np, [], 0, 0
        while frame < total:
            n = min(block, total - frame)
            while i < len(schedule) and schedule[i][0] < frame + n:
                f, _, op, ch, midi, val, part = schedule[i]
                if f > frame:
                    chunks.append(synth.render(f - frame)); n -= f - frame; frame = f
                if op == "on":
                    synth.note_on(ch, midi, val)
                elif op == "off":
                    synth.note_off(ch, midi)
                else:
                    synth.program_change(ch, val, "pluck" if val == 45 else part.instrument.family)
                i += 1
            chunks.append(synth.render(n)); frame += n
        pcm = (np.clip(np.concatenate(chunks), -1, 1) * 32767).astype("<i2")
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(samplerate)
            w.writeframes(pcm.tobytes())

    def __repr__(self) -> str:
        n = len(next(iter(self.parts.values())).measures) if self.parts else 0
        return (f"<Piece {self.title or ''!r} bpm={self.bpm:g} {self.time[0]}/{self.time[1]} "
                f"key={self.key} measures={n} parts={list(self.parts)} {self.duration_seconds():.1f}s>")


# ---------------------------------------------------------------- helpers
def _channels(n: int) -> List[int]:
    chans = [c for c in range(16) if c != 9]                     # 9 = GM drums
    if n > len(chans):
        raise RuntimeError("more than 15 parts: MIDI has only 15 melodic channels")
    return chans[:n]


def _ranges(nums: List[int]) -> str:
    out, start, prev = [], nums[0], nums[0]
    for n in nums[1:] + [None]:
        if n != prev + 1 if n is not None else True:
            out.append(f"{start}-{prev}" if start != prev else str(start))
            start = n
        prev = n if n is not None else prev
    return ", ".join(out)


def _tempo_points(initial: float, events: List[Tuple[float, Event]], end: float) -> List[Tuple[float, float]]:
    """Turn tempo marks (incl. rit/accel/a tempo) into (beat, bpm) steps."""
    points: List[Tuple[float, float]] = []
    bpm, before_ramp = initial, initial
    for i, (beat, e) in enumerate(events):
        v = e.value
        if v == "atempo":
            bpm = before_ramp
            points.append((beat, bpm))
        elif isinstance(v, tuple):                              # ('rit'|'accel', target)
            kind, target = v
            before_ramp = bpm
            if target is None:
                target = bpm * (RIT_FACTOR if kind == "rit" else ACCEL_FACTOR)
            stop = events[i + 1][0] if i + 1 < len(events) else end
            if stop <= beat:
                continue
            b = beat
            while b < stop:
                frac = (b - beat) / (stop - beat)
                points.append((b, bpm + (target - bpm) * frac))
                b += RAMP_STEP
            bpm = target
        else:
            bpm = before_ramp = float(v)
            points.append((beat, bpm))
    return points


def _apply_holds(points: List[Tuple[float, float]], initial: float,
                 holds: List[Tuple[float, float, float]]) -> List[Tuple[float, float]]:
    """Fermatas: slow the whole orchestra for the held note's span."""
    def bpm_at(beat: float) -> float:
        b = initial
        for pb, v in points:
            if pb <= beat:
                b = v
        return b
    for start, stop, factor in sorted(set(holds)):
        at_start, after = bpm_at(start), bpm_at(stop)
        inside = [(b, v / factor) for b, v in points if start < b < stop]
        points = [(b, v) for b, v in points if not (start <= b < stop)]
        points += [(start, at_start / factor)] + inside + [(stop, after)]
        points.sort()
    return sorted(set(points))
