# orch

A programming language for writing orchestral music.

You write a piece as a plain text file, one part per instrument, using note names, durations, dynamics and the other markings you would find on a printed score. The program reads the file, checks it the way a compiler checks code, and plays it with every part in sync. It can also export the piece as a MIDI file for use in any music software.

The idea comes from live-coding environments like Sonic Pi, but built around the concepts a classical musician already thinks in: instruments, measures, key signatures, ties, slurs, crescendos, repeats and tempo marks. There is no staff, no mouse and no piano roll. The text file is the composition.

## What a piece looks like

The opening phrase of Beethoven's Ode to Joy, for strings:

```
title  Ode to Joy (opening phrase)
tempo  80
time   4/4
key    D

Violin:
  1| p F4:q F4 G4 A4 |   2| A4:q G4 F4 E4 |   3| D4:q D4 E4 F4 |   4| F4:q. E4:e E4:h |

Viola:
  1| p A3:q A3 B3 A3 |   2| A3:q B3 A3 C4 |   3| A3:q A3 C4 A3 |   4| A3:q. C4:e C4:h |

Cello:
  1| p D3:q D3 G3 D3 |   2| D3:q G3 D3 A2 |   3| D3:q D3 A2 D3 |   4| D3:q. A2:e A2:h |

Contrabass:
  1| p D2:h D2:h |   2| D2:q G2 D2 A1 |   3| D2:q D2 A1 D2 |   4| D2:q. A1:e A1:h |
```

Reading it line by line:

- The header gives the title, tempo, time signature and key. The piece is in D major, so a plain `F` is F sharp and a plain `C` is C sharp, exactly as on the printed page. Write `Fn4` to force a natural.
- `Violin:` starts a part. Each measure is numbered and enclosed in bar lines: `3| ... |`.
- `F4:q` is an F in the fourth octave, held for a quarter note. A duration sticks until you change it, so `F4:q F4 G4 A4` is four quarters. `F4:q.` is a dotted quarter, `E4:e` an eighth, `E4:h` a half.
- `p` is a dynamic and applies until the next one.

Every part must contain the same measure numbers, in order, and every measure must add up to the time signature. If it does not, the program refuses to play and tells you which measure in which part is wrong. This is deliberate: the strictness is what makes a full score with a dozen parts manageable in text.

The complete notation, including chords, rests, ties, slurs, articulations, trills, grace notes, key and time changes, pizzicato, repeats and tempo marks, is in LANGUAGE.md.

## Install

You need Python 3.10 or newer.

```
pip install numpy sounddevice
```

That is enough to hear a piece through the built-in synthesizer. The synthesizer is simple and the instruments are only roughly distinguishable; it exists so that the language works with no other setup. Better sound is optional and covered below.

On Windows the command may be `py -m pip install ...`; on Mac and Linux it may be `pip3`.

## Write and play your first piece

Create a file called `first.orch` next to the `orchestra_live` folder, with this in it:

```
title  First
tempo  90
time   4/4
key    C

Cello:
  1| mf C3:q E3 G3 C4 |   2| G3:h C3:h@ |

Violin:
  1| mf E4:q G4 C5 E5 |   2| D5:h C5:h@ |
```

Then, in a terminal opened in the same folder:

```
python -m orchestra_live first.orch
```

Change a note, save, run it again. Try making measure 1 five beats long and see what the error says.

## Commands

```
python -m orchestra_live piece.orch                 play
python -m orchestra_live piece.orch --check         validate only, no sound
python -m orchestra_live piece.orch --tempo 60      play at a different tempo (also q.=60, h=40)
python -m orchestra_live piece.orch --dry           print every note event instead of playing
python -m orchestra_live piece.orch --midi p.mid    export a MIDI file
python -m orchestra_live piece.orch --wav p.wav     render audio with the built-in synthesizer
python -m orchestra_live piece.orch --insert-after 6
                                                    renumber the file to make room for a new measure 7
```

Options combine: `--midi p.mid --wav p.wav --no-play` exports both without playing.

## How the score is organised

A piece has a header, an optional `Tempo:` line, and one block per instrument.

The header fields are `title`, `tempo`, `time`, `key` and `form`. The `form` line is the order in which measures are played, and it is how repeats work: `form 1-8 1-8 9-16` plays the first eight measures twice. Because it is written once in the header rather than inside each part, every part repeats together and nothing can get out of step.

Tempo changes, ritardandos and accelerandos go on the `Tempo:` line, using the same numbered-measure syntax as the instruments: `9| 80 |  15| rit |  16| a tempo |`. They are global, so they are not allowed inside an instrument part.

Key and time signature changes go between measures inside each part: `key: G m  time: 2/4  9| ... |`. The program checks that every part agrees.

Instrument names come from a built-in table (Violin, Viola, Cello, Contrabass, Flute, Oboe, Clarinet, Bassoon, Horn, Trumpet, Trombone, Tuba, Timpani, Harp, Piano and others). Two parts on the same instrument are written `Violin I (Violin):` and `Violin II (Violin):`. Each instrument has a playable range, and the program warns when a note is outside it.

## Sound

The built-in synthesizer needs nothing beyond the install above and is fine for checking notes and rhythms.

For real instrument sounds the program can send MIDI to any synthesizer on the computer instead of making sound itself. On Windows this happens automatically: if a MIDI device is present the player prints `MIDI -> ...` and uses it. Windows ships with a basic General MIDI synthesizer; a much better one is VirtualMIDISynth (free) loaded with a sound bank such as GeneralUser GS (free). Use `--port VirtualMIDISynth` to select it.

On Mac and Linux, install `mido` and `python-rtmidi` and point the player at a DAW or a soundfont player such as FluidSynth.

Exported MIDI files open in GarageBand, MuseScore, Noteflight, Logic, Reaper and most other music software.

## Project layout

```
orchestra_live/
  notation.py     the parser: text to timed note events
  core.py         the score model, validation, playback, save and export
  clock.py        the master clock that keeps every part in sync
  backends.py     sound output: MIDI, built-in synthesizer, dry run
  instruments.py  the instrument table
  __main__.py     the command line
LANGUAGE.md       notation reference
```

## Extending the language

Every token in the notation is a regular expression paired with a handler function in `notation.py`. Adding a new marking is one function:

```python
@parser.token(r"harm")
def harmonic(m, st, p):
    ...
```

Instruments are entries in a table in `instruments.py`. Output devices implement a four-method interface in `backends.py`.

## Status

This is a personal project and the language is still changing. Files written today may need small edits to work with later versions.
