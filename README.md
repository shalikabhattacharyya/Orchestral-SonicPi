# orch — orchestral music as text

## Setup (once)
    pip install numpy sounddevice          # built-in synth (no other setup)
    pip install mido python-rtmidi         # optional: send MIDI to a DAW / soundfont

## Run
Open a terminal in this folder (the one containing `orchestra_live/`), then:

    python -m orchestra_live pieces/midsummer_cello.orch            # play
    python -m orchestra_live pieces/midsummer_cello.orch --check    # validate only
    python -m orchestra_live pieces/midsummer_cello.orch --dry      # print events, no sound
    python -m orchestra_live pieces/evening_sketch.orch --midi out.mid --wav out.wav --no-play
    python -m orchestra_live pieces/evening_sketch.orch --insert-after 6   # renumber

On Mac/Linux you may need `python3` and `pip3` instead of `python` and `pip`.

See LANGUAGE.md for the notation reference.
