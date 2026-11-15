# Copyright (c) 2026 Mike J Hebblethwaite. See LICENSE.md.
# perch_describe: describe still pictures for family archives, on this machine (local vision model via ollama).
# Output is the media-pack info JSON (coop-pack build --info), one file per picture, so it can be packed straight away.
#   python3 perch_describe.py FOLDER OUTDIR [LIMIT]
# Rules built in: no names (people are counted, never named); unsure = said as unsure, with a sure score 1-11 (never 0);
# only what can be seen; the picture's own words go in text_in_media; nothing leaves this machine.
import base64, io, json, os, re, sys, time, urllib.request
from PIL import Image

MODEL = "gemma-chuck:latest"
OLLAMA = "http://127.0.0.1:11434/api/chat"
EXT = (".jpg", ".jpeg", ".png", ".webp")
PROMPT = """Look at this photo. Reply with ONE JSON object and nothing else, with these keys:
"summary": one plain sentence of what the picture shows.
"detailed_description": 2-4 sentences: people (what they are doing, not who they are), place, time of day, clothing or objects that hint at the era.
"people_count": number of people you can see (0 if none), as a whole number.
"setting": where it is (room, outdoors, street...), or "unknown".
"era_clues": things that suggest the decade (cars, clothes, technology, print style), or "none".
"text_in_picture": list of any readable words in the picture, as written, or [].
"sure": your confidence in the summary, a whole number 1 to 11 (11 = certain, 1 = a guess). Never 0.
Do not name any person. If you are not sure of something, say so in the field, e.g. "possibly a beach".
"""


def picture_b64(path):
    im = Image.open(path).convert("RGB")
    im.thumbnail((1024, 1024))
    buf = io.BytesIO(); im.save(buf, "JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


def ask(b64):
    body = {"model": MODEL, "stream": False, "options": {"temperature": 0.2},
            "messages": [{"role": "user", "content": PROMPT, "images": [b64]}]}
    req = urllib.request.Request(OLLAMA, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=900))["message"]["content"]


def parse(raw):
    """Pull the first JSON object out of the reply, even when the model wraps it in code fences."""
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        raise ValueError("no JSON in reply")
    d = json.loads(m.group(0))
    sure = int(d.get("sure", 1))
    sure = min(11, max(1, sure))          # the scale is 1-11, never 0
    texts = d.get("text_in_picture") or []
    if isinstance(texts, str):
        texts = [texts] if texts.lower() != "none" else []
    return {
        "summary": str(d.get("summary", "")).strip(),
        "detailed_description": str(d.get("detailed_description", "")).strip(),
        "people": {"count": int(d.get("people_count") or 0), "named": False},
        "scene": {"setting": d.get("setting", "unknown"), "era_clues": d.get("era_clues", "none")},
        "text_in_media": [{"text": t, "language": None} for t in texts if str(t).strip()],
        "objects": [],
        "timeline": [],
        "audio": None,
        "sure": sure,
    }


def describe(folder, outdir, limit=10**9):
    os.makedirs(outdir, exist_ok=True)
    names = sorted(n for n in os.listdir(folder) if n.lower().endswith(EXT))[:limit]
    done = 0
    for n in names:
        dst = os.path.join(outdir, n + ".info.json")
        if os.path.exists(dst):
            continue
        try:
            info = parse(ask(picture_b64(os.path.join(folder, n))))
        except Exception as e:
            info = {"summary": None, "error": str(e)}
        info.update({"version": "1.0", "media_type": "image",
                     "generator": {"tool": "perch_describe", "model": MODEL + " (local)",
                                   "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}})
        with open(dst, "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False, indent=2)
        done += 1
        print("described", n, flush=True)
    return done


if __name__ == "__main__":
    describe(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 10**9)
