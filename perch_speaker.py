#!/usr/bin/env python3
# Copyright (c) 2026 Mike J Hebblethwaite. See LICENSE.md.
# perch_speaker.py MODEL PCM OUT.npy < windows (one "start end" in seconds per line): a learned voice print for each window, from
# an open speaker model run here (sherpa-onnx; TitaNet small by default, trained on thousands of voices). Runs in Perch's model
# environment, like perch_tag.py. PCM: 16 kHz mono 16-bit. OUT.npy: one unit-length row per window (zeros where too short).
import sys
import numpy as np
import sherpa_onnx

model, pcm, out = sys.argv[1:4]
wav = np.memmap(pcm, dtype=np.int16, mode="r")
ex = sherpa_onnx.SpeakerEmbeddingExtractor(sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=model, num_threads=4))
rows = []
for line in sys.stdin:
    a, b = (float(v) for v in line.split())
    x = np.asarray(wav[int(a * 16000):int(b * 16000)], dtype=np.float32) / 32768.0
    e = None
    if len(x) >= 8000:
        st = ex.create_stream(); st.accept_waveform(16000, x); st.input_finished()
        if ex.is_ready(st): e = np.array(ex.compute(st), dtype=np.float32)
    rows.append(e)
dim = next((len(e) for e in rows if e is not None), 192)
M = np.zeros((len(rows), dim), dtype=np.float32)
for i, e in enumerate(rows):
    if e is not None: M[i] = e / (np.linalg.norm(e) + 1e-9)
np.save(out, M)
