"""Three voices for post 6's mix-minus lab, spoken by Kokoro (Apache-2.0 weights): Alice, Bob and Carol in a meeting."""
import numpy as np, soundfile as sf
from kokoro_onnx import Kokoro
k = Kokoro("kokoro.onnx", "voices.bin")
lines = {"alice": ("af_heart", "So the plan is to ship on Friday."),
         "bob": ("am_michael", "Friday works for me, if the tests pass."),
         "carol": ("bf_emma", "I'll have the review done by Thursday.")}
for name, (voice, text) in lines.items():
    audio, rate = k.create(text, voice=voice, speed=1.0, lang="en-us")
    sf.write(f"{name}.wav", audio, rate)
    print(name, rate, round(len(audio) / rate, 2))
