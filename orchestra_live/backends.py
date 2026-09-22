"""
Output backends. All share the tiny Backend interface.

    MidiBackend   -> pip install mido python-rtmidi   (or: pip install mido pygame
                     if python-rtmidi won't build) - drives Windows' built-in GM
                     synth, a DAW, a soundfont player or hardware
    SynthBackend  -> pip install numpy sounddevice   (zero-setup fallback)
    PrintBackend  -> no deps; prints events with timestamps (dry run)

Synth is the DSP engine behind SynthBackend; it is also used offline by
Piece.export_wav, which is why it is separate from the audio stream.
"""
from __future__ import annotations

import math
import threading
import time

from .notation import midi_to_note


class Backend:
    def program_change(self, channel: int, program: int, family: str) -> None: ...
    def note_on(self, channel: int, midi: int, velocity: int) -> None: ...
    def note_off(self, channel: int, midi: int) -> None: ...
    def all_notes_off(self) -> None: ...
    def close(self) -> None: ...


# ---------------------------------------------------------------- print
class PrintBackend(Backend):
    def __init__(self, clock=None):
        self.clock, self._t0 = clock, time.perf_counter()

    def _stamp(self) -> str:
        if self.clock is not None:
            return f"beat {self.clock.now_beat():7.3f}"
        return f"t={time.perf_counter() - self._t0:7.3f}s"

    def program_change(self, channel, program, family):
        print(f"[{self._stamp()}] ch{channel:<2} program {program} ({family})")

    def note_on(self, channel, midi, velocity):
        print(f"[{self._stamp()}] ch{channel:<2} ON  {midi_to_note(midi):<4} vel {velocity}")

    def note_off(self, channel, midi):
        print(f"[{self._stamp()}] ch{channel:<2} OFF {midi_to_note(midi)}")


# ---------------------------------------------------------------- midi
class MidiBackend(Backend):
    def __init__(self, port_name: str | None = None):
        import mido
        try:
            names = mido.get_output_names()          # python-rtmidi, if installed
        except ImportError:
            mido.set_backend("mido.backends.pygame")  # fallback: pip install pygame
            names = mido.get_output_names()
        if not names:
            raise RuntimeError("no MIDI output ports found")
        if port_name is None:
            port_name = names[0]
        else:
            port_name = next((n for n in names if port_name.lower() in n.lower()), port_name)
        self._mido = mido
        self._port = mido.open_output(port_name)
        self._lock = threading.Lock()
        self._sounding: set[tuple[int, int]] = set()
        print(f"MIDI -> {port_name}")

    def _send(self, msg):
        with self._lock:
            self._port.send(msg)

    def program_change(self, channel, program, family):
        self._send(self._mido.Message("program_change", channel=channel, program=program))

    def note_on(self, channel, midi, velocity):
        self._sounding.add((channel, midi))
        self._send(self._mido.Message("note_on", channel=channel, note=midi, velocity=velocity))

    def note_off(self, channel, midi):
        self._sounding.discard((channel, midi))
        self._send(self._mido.Message("note_off", channel=channel, note=midi, velocity=0))

    def all_notes_off(self):
        for ch, m in list(self._sounding):
            self.note_off(ch, m)

    def close(self):
        self.all_notes_off()
        self._port.close()


# ---------------------------------------------------------------- windows (no packages)
class WinmmBackend(Backend):
    """Windows only: talks to the built-in MIDI synth via winmm.dll. No pip installs."""

    def __init__(self, port_name: str | None = None):
        import ctypes
        import sys
        from ctypes import wintypes
        if sys.platform != "win32":
            raise RuntimeError("WinmmBackend is Windows-only")
        self._w = ctypes.windll.winmm

        class MIDIOUTCAPS(ctypes.Structure):
            _fields_ = [("wMid", wintypes.WORD), ("wPid", wintypes.WORD),
                        ("vDriverVersion", wintypes.UINT), ("szPname", wintypes.WCHAR * 32),
                        ("wTechnology", wintypes.WORD), ("wVoices", wintypes.WORD),
                        ("wNotes", wintypes.WORD), ("wChannelMask", wintypes.WORD),
                        ("dwSupport", wintypes.DWORD)]
        names = []
        for i in range(self._w.midiOutGetNumDevs()):
            caps = MIDIOUTCAPS()
            self._w.midiOutGetDevCapsW(i, ctypes.byref(caps), ctypes.sizeof(caps))
            names.append(caps.szPname)
        if not names:
            raise RuntimeError("no MIDI output devices")
        dev = 0
        if port_name:
            dev = next((i for i, n in enumerate(names) if port_name.lower() in n.lower()), 0)
        self._h = wintypes.HANDLE()
        if self._w.midiOutOpen(ctypes.byref(self._h), dev, 0, 0, 0) != 0:
            raise RuntimeError(f"could not open MIDI device {names[dev]!r}")
        self._lock = threading.Lock()
        self._sounding: set[tuple[int, int]] = set()
        print(f"MIDI -> {names[dev]}")

    def _send(self, status: int, d1: int, d2: int = 0) -> None:
        with self._lock:
            self._w.midiOutShortMsg(self._h, status | (d1 << 8) | (d2 << 16))

    def program_change(self, channel, program, family):
        self._send(0xC0 | channel, program)

    def note_on(self, channel, midi, velocity):
        self._sounding.add((channel, midi))
        self._send(0x90 | channel, midi, velocity)

    def note_off(self, channel, midi):
        self._sounding.discard((channel, midi))
        self._send(0x80 | channel, midi, 0)

    def all_notes_off(self):
        for ch, m in list(self._sounding):
            self.note_off(ch, m)

    def close(self):
        self.all_notes_off()
        time.sleep(0.5)
        self._w.midiOutClose(self._h)


# ---------------------------------------------------------------- synth
# One recipe per instrument (keyed by GM program). partials: (ratio, amp) pairs or a
# rolloff spec; formants: (centre Hz, bandwidth, gain) resonances that colour the
# spectrum differently for every note; bright: high partials that swell in with the
# attack (brass); vib: (rate, depth); detune: extra slightly-mistuned oscillators
# (string sections); noise: breath / bow / stick noise at the attack; decay: for
# struck or plucked sounds.
def _rolloff(n, power, odd_only=False, start=1):
    return [(k, (0.08 if (odd_only and k % 2 == 0) else 1.0) / k ** power)
            for k in range(start, n + 1)]

_RECIPES = {
    # strings
    40: dict(partials=_rolloff(14, 1.0), formants=[(300, 120, 1.5), (700, 220, 1.0), (1600, 450, 0.6)],
             attack=0.12, release=0.30, vib=(5.5, 0.007), detune=(0, 0.004, -0.003), noise=0.06),
    41: dict(partials=_rolloff(14, 1.0), formants=[(220, 100, 1.6), (600, 200, 1.0), (1300, 400, 0.5)],
             attack=0.13, release=0.30, vib=(5.2, 0.006), detune=(0, 0.004, -0.003), noise=0.06),
    42: dict(partials=_rolloff(16, 1.0), formants=[(180, 80, 1.8), (450, 150, 1.2), (1000, 300, 0.5)],
             attack=0.15, release=0.35, vib=(5.0, 0.006), detune=(0, 0.003, -0.003), noise=0.07),
    43: dict(partials=_rolloff(12, 1.3), formants=[(100, 50, 2.0), (300, 120, 1.0)],
             attack=0.18, release=0.40, vib=(4.5, 0.004), detune=(0, 0.003), noise=0.08),
    45: dict(partials=_rolloff(12, 1.0), formants=[(300, 150, 1.5)], attack=0.003, release=0.25,
             decay=0.45, detune=(0, 0.003), noise=0.15),
    46: dict(partials=_rolloff(10, 1.2), formants=[(400, 200, 1.0)], attack=0.003, release=0.6, decay=1.2),
    # woodwinds
    72: dict(partials=[(1, 1.0), (2, 0.08), (3, 0.04)], attack=0.05, release=0.12, vib=(5.8, 0.005), noise=0.25),
    73: dict(partials=[(1, 1.0), (2, 0.14), (3, 0.07), (4, 0.02)], attack=0.07, release=0.15,
             vib=(5.5, 0.005), noise=0.30, breath=0.04),
    68: dict(partials=_rolloff(12, 0.9), formants=[(1000, 250, 3.0), (2400, 400, 1.5)],
             attack=0.04, release=0.12, vib=(5.5, 0.004), noise=0.05),
    69: dict(partials=_rolloff(12, 0.9), formants=[(700, 200, 3.0), (1800, 400, 1.2)],
             attack=0.05, release=0.14, vib=(5.2, 0.004), noise=0.05),
    71: dict(partials=_rolloff(13, 0.9, odd_only=True), formants=[(1500, 500, 1.0)],
             attack=0.05, release=0.12, vib=(5.0, 0.002), noise=0.04),
    70: dict(partials=_rolloff(14, 0.8), formants=[(500, 150, 3.0), (1200, 300, 1.0)],
             attack=0.06, release=0.15, vib=(5.0, 0.003), noise=0.08),
    # brass
    60: dict(partials=_rolloff(10, 1.5), formants=[(450, 150, 2.0)], attack=0.08, release=0.25,
             bright=0.5, vib=(4.5, 0.003), noise=0.02),
    56: dict(partials=_rolloff(14, 0.7), formants=[(1200, 400, 2.0), (2500, 600, 1.0)],
             attack=0.03, release=0.15, bright=1.0, vib=(5.0, 0.003), noise=0.03),
    59: dict(partials=_rolloff(14, 0.6), formants=[(2000, 400, 3.0)], attack=0.03, release=0.15, bright=1.0),
    57: dict(partials=_rolloff(12, 0.9), formants=[(600, 200, 2.0), (1500, 500, 0.8)],
             attack=0.05, release=0.18, bright=0.8, vib=(4.8, 0.003), noise=0.03),
    58: dict(partials=_rolloff(8, 1.6), formants=[(250, 100, 2.0)], attack=0.07, release=0.25, bright=0.4),
    # percussion / keyboard
    47: dict(partials=[(1, 1.0), (1.5, 0.5), (1.98, 0.35), (2.44, 0.2)], attack=0.002, release=0.3,
             decay=0.9, noise=0.6),
    9:  dict(partials=[(1, 1.0), (2.76, 0.4), (5.4, 0.15)], attack=0.002, release=0.5, decay=1.5),
    0:  dict(partials=_rolloff(10, 1.2), formants=[(500, 300, 0.8)], attack=0.003, release=0.3, decay=2.0),
    8:  dict(partials=[(1, 1.0), (4, 0.3), (10, 0.05)], attack=0.002, release=0.4, decay=1.2),
}
_FAMILY_DEFAULT = {"strings": 40, "woodwind": 73, "brass": 60, "percussion": 47, "keyboard": 0, "pluck": 45}


class Synth:
    """Additive/formant synth engine (numpy). Render in blocks; no audio I/O here."""

    def __init__(self, samplerate: int = 44100, gain: float = 0.35):
        import numpy as np
        self.np, self.sr, self.gain = np, samplerate, gain
        self._programs: dict[int, int] = {}
        self._voices: dict[tuple[int, int], dict] = {}
        self._rng = np.random.default_rng(1)

    def program_change(self, channel, program, family):
        self._programs[channel] = program if program in _RECIPES else _FAMILY_DEFAULT.get(family, 40)

    def note_on(self, channel, midi, velocity):
        np = self.np
        r = _RECIPES[self._programs.get(channel, 40)]
        freq = 440.0 * 2 ** ((midi - 69) / 12)
        ratios = np.array([p[0] for p in r["partials"]])
        amps = np.array([p[1] for p in r["partials"]], dtype=float)
        for centre, bw, gain in r.get("formants", []):
            amps *= 1 + gain / (1 + ((ratios * freq - centre) / bw) ** 2)
        amps[ratios * freq > self.sr * 0.45] = 0            # keep below Nyquist
        vel = velocity / 127
        bright = r.get("bright", 0.0) * (0.4 + vel)
        high = ratios >= 3
        static = amps * np.where(high, 1 - min(bright, 0.9), 1.0)
        swell = amps * np.where(high, min(bright, 0.9), 0.0) * (1 + bright)
        norm = float(np.sqrt(((static + swell) ** 2).sum())) or 1.0
        self._voices[(channel, midi)] = dict(
            freq=freq, ratios=ratios, static=static / norm, swell=swell / norm,
            amp=vel ** 1.6, attack=r["attack"] * self.sr, release=r["release"] * self.sr,
            decay=r.get("decay"), vib=r.get("vib", (0, 0)), detune=r.get("detune", (0,)),
            noise=r.get("noise", 0.0), breath=r.get("breath", 0.0),
            phases=[0.0] * len(r.get("detune", (0,))), age=0, rel_at=None)

    def note_off(self, channel, midi):
        v = self._voices.get((channel, midi))
        if v and v["rel_at"] is None:
            v["rel_at"] = v["age"]

    def all_notes_off(self):
        for v in self._voices.values():
            if v["rel_at"] is None:
                v["rel_at"] = v["age"]

    @property
    def active(self) -> bool:
        return bool(self._voices)

    def render(self, frames: int):
        np = self.np
        buf = np.zeros(frames)
        n = np.arange(frames)
        dead = []
        for key, v in self._voices.items():
            t = (v["age"] + n) / self.sr
            env = np.minimum((v["age"] + n) / v["attack"], 1.0)
            if v["decay"]:
                env = env * np.exp(-t / v["decay"])
            if v["rel_at"] is not None:
                env = env * np.clip(1 - (v["age"] + n - v["rel_at"]) / v["release"], 0, 1)
                if v["age"] + frames - v["rel_at"] > v["release"]:
                    dead.append(key)
            rate, depth = v["vib"]
            fm = 1 + depth * np.sin(2 * math.pi * rate * t) * np.minimum(t / 0.5, 1) if rate else 1.0
            swell_env = env ** 2
            sig = np.zeros(frames)
            for i, d in enumerate(v["detune"]):
                ph = v["phases"][i] + np.cumsum(2 * math.pi * v["freq"] * (1 + d) * fm / self.sr)
                mat = np.sin(np.outer(ph, v["ratios"]))
                sig += mat @ v["static"] + (mat * swell_env[:, None]) @ v["swell"]
                v["phases"][i] = ph[-1] % (2 * math.pi)
            sig /= len(v["detune"])
            if v["noise"] or v["breath"]:
                white = self._rng.standard_normal(frames + 7)
                soft = np.convolve(white, np.ones(8) / 8, mode="valid")
                sig += soft * (v["noise"] * np.exp(-t / 0.06) + v["breath"]) * env * 0.5
            buf += sig * env * v["amp"]
            v["age"] += frames
        for key in dead:
            del self._voices[key]
        return np.tanh(buf * self.gain)


class SynthBackend(Backend):
    """Synth engine streamed live through sounddevice."""

    def __init__(self, samplerate: int = 44100, blocksize: int = 512):
        import sounddevice as sd
        self.synth = Synth(samplerate)
        self._lock = threading.Lock()
        self._stream = sd.OutputStream(samplerate=samplerate, channels=1,
                                       blocksize=blocksize, callback=self._callback)
        self._stream.start()

    def program_change(self, channel, program, family):
        with self._lock:
            self.synth.program_change(channel, program, family)

    def note_on(self, channel, midi, velocity):
        with self._lock:
            self.synth.note_on(channel, midi, velocity)

    def note_off(self, channel, midi):
        with self._lock:
            self.synth.note_off(channel, midi)

    def all_notes_off(self):
        with self._lock:
            self.synth.all_notes_off()

    def close(self):
        time.sleep(1.0)                    # let releases finish
        self._stream.stop()
        self._stream.close()

    def _callback(self, out, frames, _time, _status):
        with self._lock:
            out[:, 0] = self.synth.render(frames)


def auto_backend(clock=None) -> Backend:
    """MIDI if a port exists (mido, or Windows' own synth), else built-in synth, else print."""
    for cls in (MidiBackend, WinmmBackend):
        try:
            return cls()
        except Exception:
            pass
    try:
        return SynthBackend()
    except Exception:
        pass
    print("No MIDI port or audio device found; using PrintBackend.")
    return PrintBackend(clock)
