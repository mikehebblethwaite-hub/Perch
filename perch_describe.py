# Copyright (c) 2026 Mike J Hebblethwaite. See LICENSE.md.
# perch_describe: describe still pictures for family archives, on this machine (local vision model via ollama).
# Output is the media-pack info JSON (coop-pack build --info), one file per picture, so it can be packed straight away.
#   python3 perch_describe.py FOLDER OUTDIR [LIMIT]
# Rules built in: no names (people are counted, never named); unsure = said as unsure, with a sure score 1-11 (never 0);
# only what can be seen; the picture's own words go in text_in_media; nothing leaves this machine.
import base64, io, json, os, re, sys, time, urllib.request
from PIL import Image
import perch_tech                                       # a reverse engineer's known rules: codes, packages, LED legs

MODEL = "gemma-chuck:latest"
OLLAMA = "http://127.0.0.1:11434/api/chat"
EXT = (".jpg", ".jpeg", ".png", ".webp")
PROMPT = """Look at this picture. Reply with ONE JSON object and nothing else, with these keys:
"kind": what sort of picture it is, one of: "photo" (a photograph of people, places or things), "technical" (an electronic
component or chip, a pin-out, a schematic or circuit, a circuit board, a datasheet page, an engineering or wiring drawing),
"document" (mostly text: a letter, a page, a form), "screenshot" (a computer or phone screen), "drawing" (art, a sketch, a cartoon),
"other".
"summary": one plain sentence of what the picture shows.
"detailed_description": 2-4 sentences: people (what they are doing, not who they are), place, time of day, clothing or objects that hint at the era.
"people_count": number of people you can see (0 if none), as a whole number.
"setting": where it is (room, outdoors, street...), or "unknown".
"era_clues": things that suggest the decade (cars, clothes, technology, print style), or "none".
"text_in_picture": list of any readable words in the picture, as written, or [].
"sure": your confidence in the summary, a whole number 1 to 11 (11 = certain, 1 = a guess). Never 0.
Do not name any person. If you are not sure of something, say so in the field, e.g. "possibly a beach".
"""


# The second look, for a technical picture only (Mike, 16 Nov 2026: "add tech chips and so on detailed scans once recognised as
# such"): what a maker needs from it, every word and number as written on it, nothing invented.
TECH_PROMPT = """This is a technical picture (an electronic part, pin-out, schematic, circuit board, datasheet or engineering
drawing). Read it carefully. Reply with ONE JSON object and nothing else, with these keys:
"what": the kind of part or drawing, e.g. "RGB LED pin-out", "8-pin chip footprint", "power supply schematic".
"part_numbers": list of part or model numbers written on it, exactly as written, or [].
"pins": list of {"pin": the pin number or name as written, "label": its label as written, "job": what it is for if the picture
says so, else null}, in pin order, or [].
"values": list of {"name": what the value is (e.g. "forward voltage", "R1"), "value": the number as written, "unit": as written}, or [].
"dimensions": list of {"what": what is measured, "value": the number as written, "unit": as written (mm, in ...)}, or [].
"view": "top", "bottom", "side" or "unknown": which way the drawing shows the part (datasheets say "top view" / "bottom view").
"pin1_mark": how pin 1 is marked if shown (a dot, a notch, a bevelled edge, a square pad, the longest leg), else null.
"bands": the colour bands of a resistor in order from the end they start at (e.g. ["brown","black","red","gold"]), or [].
"notes": anything else a maker needs (polarity, common anode or cathode, orientation marks), only if the picture shows it, else "".
"sure": how sure you are of this reading, a whole number 1 to 11 (11 = certain, 1 = a guess). Never 0.
Only what is in the picture. Do not guess a part number or a value that is not written there.
"""
TECH_SIZE = 1600                                        # a technical picture is read larger: small print, pin numbers


def picture_b64(path, size=1024):
    im = Image.open(path).convert("RGB")
    im.thumbnail((size, size))
    buf = io.BytesIO(); im.save(buf, "JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


def ask(b64, prompt=PROMPT):
    body = {"model": MODEL, "stream": False, "options": {"temperature": 0.2},
            "messages": [{"role": "user", "content": prompt, "images": [b64]}]}
    req = urllib.request.Request(OLLAMA, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=900))["message"]["content"]


KINDS = ("photo", "technical", "document", "screenshot", "drawing", "other")


def first_json(raw):
    """The first JSON object in a reply, even when the model wraps it in code fences."""
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        raise ValueError("no JSON in reply")
    return json.loads(m.group(0))


def sure_of(v):
    try: return min(11, max(1, int(v)))                 # the scale is 1-11, never 0
    except (TypeError, ValueError): return 1


def parse(raw):
    d = first_json(raw)
    sure = sure_of(d.get("sure"))         # the scale is 1-11, never 0
    texts = d.get("text_in_picture") or []
    if isinstance(texts, str):
        texts = [texts] if texts.lower() != "none" else []
    kind = str(d.get("kind", "")).strip().lower()
    return {
        "kind": kind if kind in KINDS else "other",
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


def parse_tech(raw):
    """The second look's reading, every field checked; anything else in the reply is dropped."""
    d = first_json(raw)
    txt = lambda v: None if v is None else str(v).strip()[:200]
    lst = lambda v: v if isinstance(v, list) else []
    return {
        "what": txt(d.get("what")) or "",
        "part_numbers": [txt(x) for x in lst(d.get("part_numbers")) if txt(x)],
        "pins": [{"pin": txt(x.get("pin")), "label": txt(x.get("label")), "job": txt(x.get("job"))}
                 for x in lst(d.get("pins")) if isinstance(x, dict)],
        "values": [{"name": txt(x.get("name")), "value": txt(x.get("value")), "unit": txt(x.get("unit"))}
                   for x in lst(d.get("values")) if isinstance(x, dict)],
        "dimensions": [{"what": txt(x.get("what")), "value": txt(x.get("value")), "unit": txt(x.get("unit"))}
                       for x in lst(d.get("dimensions")) if isinstance(x, dict)],
        "view": (txt(d.get("view")) or "unknown").lower(),
        "pin1_mark": txt(d.get("pin1_mark")),
        "bands": [txt(x) for x in lst(d.get("bands")) if txt(x)],
        "notes": txt(d.get("notes")) or "",
        "sure": sure_of(d.get("sure")),
    }


def describe_one(path):
    """One picture: the first look; a technical one gets the second, detailed look (read larger). Returns the info."""
    info = parse(ask(picture_b64(path)))
    looks = ["general"]
    if info["kind"] == "technical":
        try:
            info["technical"] = parse_tech(ask(picture_b64(path, TECH_SIZE), TECH_PROMPT))
            words = [t["text"] for t in info.get("text_in_media", [])]
            if info["technical"]["view"] == "bottom": words.append("bottom view")
            info["technical"]["derived"] = perch_tech.derive(info["technical"], words)   # what the reading means, by known rules
            if info["technical"]["bands"]:
                v = perch_tech.colour_bands(info["technical"]["bands"])
                if v: info["technical"]["derived"].append(perch_tech.fact(f"resistor bands {'-'.join(info['technical']['bands'])} = {v}",
                                                                          "resistor colour code", 9))
        except Exception as e:
            info["technical"] = {"error": f"{type(e).__name__}: {e}"}
        looks.append("technical")
    info["looks"] = looks
    return info


def describe(folder, outdir, limit=10**9):
    os.makedirs(outdir, exist_ok=True)
    names = sorted(n for n in os.listdir(folder) if n.lower().endswith(EXT))[:limit]
    done = 0
    for n in names:
        dst = os.path.join(outdir, n + ".info.json")
        if os.path.exists(dst):
            continue
        try:
            info = describe_one(os.path.join(folder, n))
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
