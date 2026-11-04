# Copyright (c) 2026 Mike J Hebblethwaite. See LICENSE.md.
# perch_settings: how Perch answers a look, the same for web pages, a display and VMs.
#   ~/.config/perch/settings.json   the person's own choice (perch-settings, the settings page, writes it)
#   PERCH_MODE / PERCH_CUT / PERCH_ZOOM  for one shell or one bot
#   --mode X  --cut N  --zoom N          for one call (wins over both)
# Modes:
#   auto     words, each line of a full look cut short; when even those would cost more than a zoomed-out picture, the picture
#   words    the screen's own words (from the page or the apps), each line of a full look cut short (`read N` / all: whole)
#   ocr      words read from the pixels here on the machine (no tokens to read them), even where the app gives words
#   picture  a zoomed-out picture of the screen (zoom % of its size): a fixed price however busy the screen is
# A look after an action stays "only what changed" in words, auto and ocr; picture mode answers every look with a picture.
import json, os, re

MODES = ("auto", "words", "ocr", "picture")
DEFAULTS = {"mode": "auto", "cut": 60, "zoom": 50}
PATH = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "perch", "settings.json")


def check(k, v):
    """a setting's value, or ValueError naming what is allowed (the file, the environment and the flags all pass through here)"""
    if k == "mode":
        v = str(v).strip().lower()
        if v not in MODES: raise ValueError("mode: " + " | ".join(MODES))
        return v
    if k in ("cut", "zoom"):
        try: n = int(str(v).strip())
        except ValueError: raise ValueError(f"{k}: a whole number") from None
        if k == "cut" and not (n == 0 or 20 <= n <= 400): raise ValueError("cut: 20 to 400 characters a line, or 0 for whole lines")
        if k == "zoom" and not 25 <= n <= 100: raise ValueError("zoom: 25 to 100 (% of the screen's size)")
        return n
    raise ValueError(f"no setting '{k}' (mode, cut, zoom)")


def saved():
    """the person's saved choices, each checked; a bad or missing file is the defaults (never a crash in a bot's look)"""
    out = dict(DEFAULTS)
    try:
        d = json.load(open(PATH))
        for k in DEFAULTS:
            if k in d:
                try: out[k] = check(k, d[k])
                except ValueError: pass
    except (OSError, ValueError):
        pass
    return out


def save(s):
    s = {k: check(k, s.get(k, DEFAULTS[k])) for k in DEFAULTS}
    os.makedirs(os.path.dirname(PATH), mode=0o700, exist_ok=True)
    tmp = PATH + ".new"
    with open(tmp, "w") as f: json.dump(s, f, indent=1); f.write("\n")
    os.replace(tmp, PATH)
    return s


def load(args):
    """(settings, args without the flags): saved < environment < --flags. A bad flag raises ValueError (the caller says so)."""
    s = saved()
    for k in DEFAULTS:
        e = os.environ.get("PERCH_" + k.upper())
        if e:
            try: s[k] = check(k, e)
            except ValueError: pass
    rest, i = [], 0
    while i < len(args):
        a = args[i]
        m = re.fullmatch(r"--(mode|cut|zoom)(?:=(.*))?", a)
        if m:
            v = m.group(2)
            if v is None:
                if i + 1 >= len(args): raise ValueError(f"--{m.group(1)} needs a value")
                i += 1; v = args[i]
            s[m.group(1)] = check(m.group(1), v)
        else:
            rest.append(a)
        i += 1
    return s, rest


QUOTED = re.compile(r"'((?:[^'\\]|\\.)*)'")


def cut(text, n):
    """every quoted part longer than n characters cut to n, ending in … (n = 0: whole); escapes are never split"""
    if not n: return text
    def one(m):
        body = m.group(1)
        if len(body) <= n: return m.group(0)
        parts = re.findall(r"\\.|.", body)               # an escape (\' \\ \n) counts as one character and is kept whole
        if len(parts) <= n: return m.group(0)
        return "'" + "".join(parts[:n]) + "…'"
    return QUOTED.sub(one, text)


def tokens_words(text):
    return (len(text) + 3) // 4                         # an estimate: about 4 characters a token for these lines


def tokens_picture(w, h):
    return w * h // 750                                 # Anthropic's published rule for an image (w*h/750)


def picture(im, zoom, path):
    """the whole screen at zoom % of its size, saved as a PNG -> the answer line a bot gets ("pixels FILE wxh ~T tokens")"""
    from PIL import Image
    w, h = max(1, im.width * zoom // 100), max(1, im.height * zoom // 100)
    if (w, h) != (im.width, im.height): im = im.resize((w, h), Image.LANCZOS)
    im.convert("RGB").save(path, "PNG")
    return f"pixels screen {path} {w}x{h} ~{tokens_picture(w, h)} tokens"
