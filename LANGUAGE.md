# .orch language reference

## File layout
```
title  Evening Sketch
tempo  q.=88            # or 96 (quarter = 96), h=60
time   3/8
key    G m              # G / G m / Bb / Eb major
form   1-8 1-8 9-16     # play order; default: 1 to the end, once

Tempo:                  # optional - the only place tempo marks may appear
  9| 80 |  15| rit |  16| a tempo |

Cello:                  # "Name:" or "Name (Instrument):"
  1| mp B2:h. |  2| F2:h. |
```
`#` starts a comment anywhere.

## Measures
| | |
|---|---|
| `12\| ... \|` | measure 12; numbers are required, sequential, and every part must have the same ones |
| `0\| D4:q \|` | pickup measure (the only one allowed to be short) |
| `5-8\| \|` | multi-measure rest |
| `key: G m` `time: 2/4` | written **between** measures; apply from the next one |
| `&` | divisi: second voice restarting at the top of this measure |

Every measure must add up to the time signature exactly, or the file won't load.

## Notes
| | |
|---|---|
| `C4` `F#3` `Bb2` `En4` | key signature applies to plain letters; `n` forces a natural |
| `:q` | duration: `w h q e s t`, dotted `q.`, tuplet `e3`, or beats `:1.5` — sticky until changed |
| `[C4 E4 G4]:h` | chord |
| `r:q` | rest |
| `o5` | default octave for notes written without one |

## Marks on a note
| | |
|---|---|
| prefix `>` `^` `.` `_` | accent, marcato, staccato, tenuto — `>.C4:q` |
| suffix `~` `@` `*` | tie, fermata (everyone holds), tremolo — `C4:h~` |
| before the note: `sfz` `sf` `rfz` `fp` | one-note accents; `fp` then continues at p |
| `tr C4:q` | trill (diatonic upper neighbour in the current key) |
| `{D4} C4:q` | grace note(s) |

## Sticky modes
`stacc` `legato` `ord` — `pizz` `arco` — `con sord` `senza sord`

## Dynamics and slurs
`ppp pp p mp mf f ff fff` · `cresc`/`<` and `dim`/`>` ramp until the next dynamic · `( ... )` slur

## Tempo line
`96` · `q.=88` · `rit` · `rit:60` · `accel` · `accel:120` · `a tempo`

## Commands
```
python -m orchestra_live piece.orch                 play
python -m orchestra_live piece.orch --check         validate only
python -m orchestra_live piece.orch --dry           print events, no sound
python -m orchestra_live piece.orch --midi p.mid --wav p.wav --no-play
python -m orchestra_live piece.orch --insert-after 6    make room for a new measure 7
```
