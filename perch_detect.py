# Copyright (c) 2026 Mike J Hebblethwaite. See LICENSE.md.
"""perch_detect: what is it? A small neural detector run on our own hardware, the same kind of wheel a camera's own AI uses
(person / pet), with birds and the rest of its 80 everyday classes as well. The model is YOLOX-s (Megvii, Apache-2.0), 640x640,
COCO classes; the file is fetched once into ~/.cache/perch-models/detect (never in the repo).

  python3 perch_detect.py IMAGE_OR_VIDEO... [--min 0.25] [--every N]   one line per thing found: file [t] class sure x0,y0,x1,y1
  from perch_detect import Detector; Detector().run(bgr_uint8_frame) -> [(class, score, (x0, y0, x1, y1)), ...]

Sure is the model's own score (0..1); for people we say it on the 1-11 scale (sure11).
"""
import os, subprocess, sys
import numpy as np

MODEL = os.environ.get("PERCH_DETECT_MODEL", os.path.expanduser("~/.cache/perch-models/detect/yolox_s.onnx"))
COCO = ("person bicycle car motorcycle airplane bus train truck boat traffic-light fire-hydrant stop-sign parking-meter bench bird cat "
        "dog horse sheep cow elephant bear zebra giraffe backpack umbrella handbag tie suitcase frisbee skis snowboard sports-ball kite "
        "baseball-bat baseball-glove skateboard surfboard tennis-racket bottle wine-glass cup fork knife spoon bowl banana apple sandwich "
        "orange broccoli carrot hot-dog pizza donut cake chair couch potted-plant bed dining-table toilet tv laptop mouse remote keyboard "
        "cell-phone microwave oven toaster sink refrigerator book clock vase scissors teddy-bear hair-drier toothbrush").split()


def sure11(score):
    return max(1, min(11, int(round(1 + 10 * score))))


class Detector:
    def __init__(self, model=MODEL, size=640, threads=0):
        import onnxruntime as ort
        o = ort.SessionOptions()
        if threads: o.intra_op_num_threads = threads
        self.s = ort.InferenceSession(model, o, providers=["CPUExecutionProvider"]); self.n = size
        self.inp = self.s.get_inputs()[0].name
        g, st = [], []
        for stride in (8, 16, 32):
            k = size // stride; yy, xx = np.meshgrid(np.arange(k), np.arange(k), indexing="ij")
            g.append(np.stack([xx, yy], -1).reshape(-1, 2)); st.append(np.full((k * k, 1), stride))
        self.grid = np.concatenate(g).astype(np.float32); self.stride = np.concatenate(st).astype(np.float32)

    def run(self, bgr, min_score=0.25, iou=0.45):
        h, w = bgr.shape[:2]; r = min(self.n / h, self.n / w)
        nh, nw = int(round(h * r)), int(round(w * r))
        ys = (np.arange(nh) / r).astype(int).clip(0, h - 1); xs = (np.arange(nw) / r).astype(int).clip(0, w - 1)
        pad = np.full((self.n, self.n, 3), 114, np.uint8); pad[:nh, :nw] = bgr[ys][:, xs]      # letterbox (nearest, no extra libs)
        x = pad.transpose(2, 0, 1)[None].astype(np.float32)
        out = self.s.run(None, {self.inp: x})[0][0]
        xy = (out[:, :2] + self.grid) * self.stride; wh = np.exp(out[:, 2:4]) * self.stride
        sc = out[:, 4:5] * out[:, 5:]; cls = sc.argmax(1); best = sc[np.arange(len(sc)), cls]
        keep = best >= min_score
        if not keep.any(): return []
        b = np.concatenate([xy - wh / 2, xy + wh / 2], 1)[keep] / r; best = best[keep]; cls = cls[keep]
        found = []
        for c in np.unique(cls):                                                     # non-max suppression per class
            m = np.where(cls == c)[0]; m = m[np.argsort(-best[m])]
            while len(m):
                i = m[0]; found.append((COCO[c], float(best[i]), tuple(float(v) for v in b[i])))
                if len(m) == 1: break
                o = b[m[1:]]; ix0 = np.maximum(b[i, 0], o[:, 0]); iy0 = np.maximum(b[i, 1], o[:, 1])
                ix1 = np.minimum(b[i, 2], o[:, 2]); iy1 = np.minimum(b[i, 3], o[:, 3])
                inter = np.clip(ix1 - ix0, 0, None) * np.clip(iy1 - iy0, 0, None)
                area = lambda q: (q[..., 2] - q[..., 0]) * (q[..., 3] - q[..., 1])
                m = m[1:][inter / (area(b[i]) + area(o) - inter + 1e-9) < iou]
        return sorted(found, key=lambda f: -f[1])


def frames(path, every=1):
    """(t, bgr) from an image or a video, decoded by ffmpeg"""
    p = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height,r_frame_rate",
                        "-of", "csv=p=0", path], capture_output=True, text=True).stdout.strip().split("\n")[0].split(",")
    w, h = int(p[0]), int(p[1]); a, b = (p[2].split("/") + ["1"])[:2]; fps = float(a) / float(b or 1) if float(b or 1) else 0
    ff = subprocess.Popen(["ffmpeg", "-v", "error", "-i", path, "-f", "rawvideo", "-pix_fmt", "bgr24", "-"], stdout=subprocess.PIPE)
    i = 0
    while True:
        raw = ff.stdout.read(w * h * 3)
        if len(raw) < w * h * 3: break
        if i % every == 0: yield (i / fps if fps else 0), np.frombuffer(raw, np.uint8).reshape(h, w, 3)
        i += 1


if __name__ == "__main__":
    a = sys.argv[1:]; mn = float(a[a.index("--min") + 1]) if "--min" in a else 0.25; ev = int(a[a.index("--every") + 1]) if "--every" in a else 1
    files = [x for i, x in enumerate(a) if not x.startswith("--") and (i == 0 or a[i - 1] not in ("--min", "--every"))]
    d = Detector()
    for f in files:
        any_ = False
        for t, img in frames(f, ev):
            for c, s, (x0, y0, x1, y1) in d.run(img, mn):
                any_ = True; print(f"{os.path.basename(f)} {t:.2f}s {c} sure {sure11(s)}/11 ({s:.2f}) {x0:.0f},{y0:.0f},{x1:.0f},{y1:.0f}")
        if not any_: print(f"{os.path.basename(f)} nothing at {mn}")
