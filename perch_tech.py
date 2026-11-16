# Copyright (c) 2026 Mike J Hebblethwaite. See LICENSE.md.
# perch_tech: what a reverse engineer already knows about a part, as code (the wheel is already made: published conventions,
# not guesses). The vision model READS a technical picture (perch_describe's second look); this works out what the reading MEANS:
#   capacitor codes (104 -> 100 nF), resistor codes (472 -> 4.7 kOhm, 4R7), colour bands, date codes (YYWW, said as a guess),
#   package families and their pin counts, LED and RGB LED leg rules (common pin, addressable or not), pin counting.
# Every derived fact says how it was worked out ("how") and how sure it is (1-11, never 0). Nothing is looked up online.
import re

# ---- capacitors and resistors: EIA codes ----
def cap_code(code):
    """'104' -> 100 nF: two digits then the number of zeros, in picofarads. '1R5' style (R = point) -> pF. None if not a code."""
    c = str(code).strip().upper()
    m = re.fullmatch(r"(\d)(\d)(\d)", c)
    if m:
        a, b, z = map(int, m.groups())
        if z > 6: return None
        return pf_text((a * 10 + b) * 10 ** z)
    m = re.fullmatch(r"(\d)R(\d)", c)
    if m: return f"{m.group(1)}.{m.group(2)} pF"
    return None


def pf_text(pf):
    for div, unit in ((1e6, "µF"), (1e3, "nF")):
        if pf >= div:
            v = pf / div
            return f"{v:g} {unit}"
    return f"{pf:g} pF"


def res_code(code):
    """SMD resistor code: '472' -> 4.7 kΩ, '1002' -> 10 kΩ, '4R7' -> 4.7 Ω, '0' / '000' -> 0 Ω (a link). None if not a code."""
    c = str(code).strip().upper()
    if c in ("0", "00", "000", "0000"): return "0 Ω (a link)"
    m = re.fullmatch(r"(\d+)R(\d+)", c) or re.fullmatch(r"R(\d+)", c)
    if m:
        if len(m.groups()) == 1: return ohm_text(float("0." + m.group(1)))
        return ohm_text(float(f"{m.group(1)}.{m.group(2)}"))
    m = re.fullmatch(r"(\d{2,3})(\d)", c)
    if m:
        z = int(m.group(2))
        if z > 7: return None
        return ohm_text(int(m.group(1)) * 10 ** z)
    return None


def ohm_text(o):
    for div, unit in ((1e6, "MΩ"), (1e3, "kΩ")):
        if o >= div: return f"{o / div:g} {unit}"
    return f"{o:g} Ω"


COLOURS = {"black": 0, "brown": 1, "red": 2, "orange": 3, "yellow": 4, "green": 5, "blue": 6, "violet": 7, "purple": 7,
           "grey": 8, "gray": 8, "white": 9}
MULT = dict(COLOURS, gold=-1, silver=-2)
TOL = {"brown": "±1%", "red": "±2%", "green": "±0.5%", "blue": "±0.25%", "violet": "±0.1%", "grey": "±0.05%", "gold": "±5%", "silver": "±10%"}


def colour_bands(bands):
    """['brown','black','red','gold'] -> '1 kΩ ±5%'. 4 bands (2 digits) or 5/6 bands (3 digits). None if they do not make a value."""
    b = [str(x).strip().lower() for x in bands]
    if len(b) == 4: digits, mult, tol = b[:2], b[2], b[3]
    elif len(b) in (5, 6): digits, mult, tol = b[:3], b[3], b[4]
    else: return None
    if any(d not in COLOURS for d in digits) or mult not in MULT or tol not in TOL: return None
    v = int("".join(str(COLOURS[d]) for d in digits)) * 10 ** MULT[mult]
    return f"{ohm_text(round(v, 4))} {TOL[tol]}"


def date_code(code):
    """'2314' -> 'probably 2023, week 14' (YYWW, the common form: said as a guess, makers differ). None if it cannot be one."""
    c = str(code).strip()
    if not re.fullmatch(r"\d{4}", c): return None
    yy, ww = int(c[:2]), int(c[2:])
    if not 1 <= ww <= 53: return None
    return f"probably 20{yy:02d}, week {ww}" if yy <= 40 else f"probably 19{yy:02d}, week {ww}"


# ---- packages: the family a name belongs to and how many pins it has ----
PACKAGES = [
    (r"\bTO-?92\b", "TO-92", 3, "small transistor/regulator, flat face with writing: pins 1-2-3 left to right facing the flat side, legs down"),
    (r"\bTO-?220\b", "TO-220", 3, "power package with a metal tab (the tab is usually joined to the middle pin)"),
    (r"\bSOT-?23(?:-(\d))?\b", "SOT-23", 3, "tiny surface-mount, 3 pins (SOT-23-5 / -6 have 5 / 6)"),
    (r"\b(?:DIP|PDIP)-?(\d+)\b", "DIP", None, "through-hole, two rows; pin 1 by the notch or dot, counted anticlockwise seen from the top"),
    (r"\b(?:SOIC|SO)-?(\d+)\b", "SOIC", None, "surface-mount, two rows; pin 1 by the dot or bevelled edge, anticlockwise from the top"),
    (r"\b(?:TSSOP|SSOP|MSOP)-?(\d+)\b", "small-outline", None, "fine-pitch surface-mount, two rows; counted like SOIC"),
    (r"\b(?:QFN|DFN)-?(\d+)\b", "QFN", None, "no legs, pads underneath, often a big centre pad (usually ground); pin 1 by a dot"),
    (r"\b(?:LQFP|TQFP|QFP)-?(\d+)\b", "QFP", None, "four rows of legs; pin 1 by a dot, anticlockwise from the top"),
    (r"\bBGA-?(\d+)?\b", "BGA", None, "balls underneath in a grid named by letter and number (A1 is the corner marked)"),
    (r"\b5050\b", "5050 LED", None, "5 x 5 mm surface-mount LED (often 4 or 6 pads; WS2812-style addressable parts use this size)"),
]


def package(text):
    """The package family named in the text, with its pin count when the name says it: (family, pins, note) or None."""
    for pat, fam, pins, note in PACKAGES:
        m = re.search(pat, str(text), re.I)
        if m:
            n = next((int(g) for g in m.groups() if g), None) if m.groups() else None
            return fam, n or pins, note
    return None


# ---- LEDs ----
ADDR_IN = {"din", "di", "data in", "data_in", "sdi"}
ADDR_OUT = {"dout", "do", "data out", "data_out", "sdo"}
COLOUR_PINS = {"r": "red", "red": "red", "g": "green", "green": "green", "b": "blue", "blue": "blue"}


def led_rules(labels):
    """From the pin labels as written: an RGB LED with a shared pin, or an addressable LED, or nothing. A list of facts."""
    low = [str(x).strip().lower() for x in labels if x is not None and str(x).strip()]
    facts = []
    if any(l in ADDR_IN for l in low) and any(l in ADDR_OUT for l in low):
        facts.append(fact("an addressable LED: it has a data-in and a data-out pin, so LEDs chain one to the next and each is "
                          "set by data (WS2812-style)", "pins labelled data in / data out", 9))
        return facts
    colours = {COLOUR_PINS[l] for l in low if l in COLOUR_PINS}
    common = [l for l in low if "common" in l or l in ("com", "anode", "cathode", "+", "-", "a", "k")]
    if colours == {"red", "green", "blue"} and common:
        kind = ("common anode (the shared pin goes to +)" if any("anode" in c or c in ("+", "a") for c in common) else
                "common cathode (the shared pin goes to ground)" if any("cathode" in c or c in ("-", "k") for c in common) else None)
        facts.append(fact("a plain RGB LED: one leg each for red, green and blue and one shared leg; NOT addressable (no data pins), "
                          "so each colour is driven on its own leg", "pins labelled red, green, blue and a common pin", 10))
        if kind:
            facts.append(fact(kind, "the common pin is labelled so", 10))
        else:
            facts.append(fact("common anode or common cathode is not shown: on a real part the shared leg is the longest; test with "
                              "a resistor and a low voltage before wiring", "no anode/cathode mark in the reading", 6))
    return facts


def fact(text, how, sure):
    return {"fact": text, "how": how, "sure": max(1, min(11, int(sure)))}


# ---- the whole reading ----
def derive(tech, words=()):
    """What the reading means, worked out by the conventions above. tech = perch_describe's 'technical' reading;
    words = every word read off the picture. Returns a list of facts (fact, how, sure)."""
    if not isinstance(tech, dict) or "error" in tech: return []
    facts = []
    labels = [p.get("label") for p in tech.get("pins", []) if isinstance(p, dict)] + list(words)
    facts += led_rules(labels)
    seen_text = " ".join([tech.get("what", ""), tech.get("notes", ""), *tech.get("part_numbers", []), *map(str, words)])
    pk = package(seen_text)
    if pk:
        fam, pins, note = pk
        facts.append(fact(f"package {fam}" + (f", {pins} pins" if pins else "") + f": {note}", "the package name written on it", 9))
        n = len(tech.get("pins", []))
        if pins and n and n != pins:
            facts.append(fact(f"the reading lists {n} pins but a {fam} has {pins}: look again at the picture", "pin count check", 8))
    for v in tech.get("values", []):
        if not isinstance(v, dict): continue
        val, name = str(v.get("value") or ""), str(v.get("name") or "").lower()
        if not v.get("unit"):
            c = cap_code(val) if ("cap" in name or name.startswith("c")) else None
            r = res_code(val) if ("res" in name or name.startswith("r")) else None
            if c: facts.append(fact(f"{v.get('name')}: code {val} = {c}", "EIA capacitor code (two digits, then zeros, in pF)", 10))
            if r: facts.append(fact(f"{v.get('name')}: code {val} = {r}", "EIA resistor code (digits, then zeros; R = point)", 10))
    for w in words:
        d = date_code(w)
        if d and re.fullmatch(r"\d{4}", str(w).strip()) and any(k in seen_text.lower() for k in ("date", "lot", "yyww")):
            facts.append(fact(f"{w}: {d}", "YYWW date code (the common form; makers differ)", 5))
    if any(re.search(r"bottom view", str(x), re.I) for x in [tech.get("notes", ""), *words]):
        facts.append(fact("this drawing is a BOTTOM view: pins run the other way round when the part is seen from the top",
                          "the drawing says bottom view", 10))
    return facts
