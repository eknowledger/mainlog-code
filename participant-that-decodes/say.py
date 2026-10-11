"""Ana, Ben and Chloe's lines for the post's listening lab, spoken by Kokoro (Apache-2.0 weights). Run from a folder
holding kokoro.onnx and voices.bin; writes ana.wav, ben.wav and chloe.wav."""
import soundfile as sf
from kokoro_onnx import Kokoro

k = Kokoro("kokoro.onnx", "voices.bin")
lines = {
    "ana": ("af_heart", "Let's move the launch to Thursday."),
    "ben": ("am_michael", "Thursday works, but QA needs the build by Tuesday."),
    "chloe": ("bf_emma", "I can get it to them on Monday."),
}
for name, (voice, text) in lines.items():
    audio, rate = k.create(text, voice=voice, speed=1.0, lang="en-us")
    sf.write(f"{name}.wav", audio, rate)
    print(name, rate, round(len(audio) / rate, 2))
