# Late is lost: jitter buffers, concealment and forward error correction

From the post [Late is lost](https://mainlog.dev/posts/late-is-lost/), the second in the series *Real-time voice, from
the packet up*.

| File | What it does |
|---|---|
| `fec_recovery.py` | Encodes a second of a synthetic voiced sound with Opus, FEC on, loses one packet and stands in for it three ways: silence, concealment, and the FEC copy in the next packet. Then loses every frame in turn and scores each way against what would have played with no loss |
| `requirements.txt` | PyAV, which ships libopus in its wheels for the encoder, and NumPy |
| `lab-voice/say.py` | Speaks Alice's exchange for the post's in-browser lab, with [Kokoro](https://huggingface.co/hexgrad/Kokoro-82M) (Apache-2.0 weights) through `kokoro-onnx`. Needs `pip install kokoro-onnx soundfile` and the model files `kokoro-v1.0.fp16.onnx` and `voices-v1.0.bin` from the kokoro-onnx releases, saved as `kokoro.onnx` and `voices.bin` beside it |

## Run it

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python fec_recovery.py
```

It prints:

```text
decoder libopus 1.6.1; 32 kb/s, FEC on, planning for 10% loss
50 frames of 20 ms; frame 25 is lost; packet 26 is 80 B, carries an FEC copy
  silence                      0.0 dB SNR over the lost 20 ms
  concealment (PLC)            6.5 dB SNR over the lost 20 ms
  FEC from the next packet    14.1 dB SNR over the lost 20 ms
  28 of 44 packets carry an FEC copy of the frame before them
  concealment (PLC)            3.9 dB on average over 44 positions, median 4.7, worst -1.9
                               5.4 dB where the next packet carries a copy (28 positions)
  FEC from the next packet    13.5 dB on average over 44 positions, median 17.7, worst -1.9
                              20.4 dB where the next packet carries a copy (28 positions)
```

## Why it is built this way

- **One libopus, two ways in.** PyAV encodes, because its options switch FEC on (`fec`, `packet_loss`). The same
  libopus, the copy inside PyAV's wheel, decodes through ctypes, because `opus_decode(..., decode_fec=1)`, the call
  that reads the copy, is not exposed by PyAV.
  The encoder settings cannot go through ctypes as well: `opus_encoder_ctl` takes a variable argument list, which
  ctypes does not pass correctly on every platform.
- **A synthetic voice.** Harmonics of 140 Hz under two formants, pulsed four times a second like syllables. It is
  reproducible, and Opus treats it as speech, which matters: in-band FEC lives in its speech mode, and the encoder
  only adds a copy for frames it judges important. On this signal it skipped the quiet troughs between pulses.
- **SNR against the lossless decode**, over the lost 20 ms only. It measures how close the stand-in is to what
  would have played, not how it sounds; a perceptual score would need a reference implementation this demo does
  not ship.

The numbers can move by a tenth of a dB between machines.
