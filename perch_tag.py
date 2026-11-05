# Copyright (c) 2026 Mike J Hebblethwaite. See LICENSE.md.
# perch_tag: name the sounds in a recording with YAMNet (Google's open sound tagger: 521 kinds, from AudioSet), on this machine.
# Runs under its own Python (the one with ai_edge_litert): perch-video calls it as a separate program.
#   python3 perch_tag.py MODEL_DIR AUDIO [START END]  ->  JSON lines {"t0":..,"t1":..,"name":..,"score":..}, one per run of a sound
# MODEL_DIR holds yamnet.tflite and yamnet_class_map.csv. A run: the same top sound over neighbouring windows (0.975 s each,
# every 0.4875 s). Scores under 0.25 are left out (the tagger is not sure); "Silence" is left out (the s lines say quiet).
import csv, json, os, subprocess, sys

import numpy as np
from ai_edge_litert.interpreter import Interpreter

RATE, WIN, HOP, MIN = 16000, 15600, 7800, 0.25


def main():
    mdir, src = sys.argv[1], sys.argv[2]
    start = float(sys.argv[3]) if len(sys.argv) > 3 else 0.0
    end = float(sys.argv[4]) if len(sys.argv) > 4 and sys.argv[4] != "-" else None
    names = [r["display_name"] for r in csv.DictReader(open(os.path.join(mdir, "yamnet_class_map.csv")))]
    it = Interpreter(model_path=os.path.join(mdir, "yamnet.tflite")); it.allocate_tensors()
    inp, out = it.get_input_details()[0]["index"], it.get_output_details()[0]["index"]
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin"] + (["-ss", str(start)] if start else []) + ["-i", src] + \
          (["-t", str(end - start)] if end else []) + ["-vn", "-ac", "1", "-ar", str(RATE), "-f", "f32le", "-"]
    x = np.frombuffer(subprocess.run(cmd, capture_output=True, timeout=1800).stdout, dtype="<f4")
    if len(x) < WIN: x = np.pad(x, (0, WIN - len(x)))
    run, held = None, None                       # held: the last sound named, joined with the same sound after a short unsure gap
    def say(r):
        print(json.dumps({"t0": round(r[0], 2), "t1": round(r[1], 2), "name": r[2], "score": round(r[3], 2)}), flush=True)
    def emit(r):
        nonlocal held
        if not r or r[2] == "Silence" or r[3] < MIN: return
        if held and held[2] == r[2] and r[0] <= held[1] + 0.6: held[1] = r[1]; held[3] = max(held[3], r[3]); return
        if held: say(held)
        held = list(r)
    for i in range(0, len(x) - WIN + 1, HOP):
        it.set_tensor(inp, x[i:i + WIN].astype(np.float32)); it.invoke()
        s = it.get_tensor(out)[0]; k = int(np.argmax(s)); t0 = start + i / RATE; t1 = t0 + WIN / RATE
        if run and run[2] == names[k]: run[1] = t1; run[3] = max(run[3], float(s[k]))
        else: emit(run); run = [t0, t1, names[k], float(s[k])]
    emit(run)
    if held: say(held)


if __name__ == "__main__":
    main()
