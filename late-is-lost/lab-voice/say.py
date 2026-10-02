"""Alice's exchange, spoken by Kokoro (Apache-2.0 weights), for the lab's speech source."""
import numpy as np, soundfile as sf
from kokoro_onnx import Kokoro

k = Kokoro("kokoro.onnx", "voices.bin")
lines = [
    ("af_heart", "Hey, what's the weather?"),
    ("am_michael", "Right now in Paris it's fourteen degrees and cloudy, with light rain on the way this afternoon. You might want an umbrella."),
]
parts = []
rate = None
for voice, text in lines:
    audio, rate = k.create(text, voice=voice, speed=1.0, lang="en-us")
    parts += [audio, np.zeros(int(rate * 0.45), dtype=audio.dtype)]
out = np.concatenate(parts)
sf.write("alice.wav", out, rate)
print(rate, round(len(out) / rate, 2), "s")
