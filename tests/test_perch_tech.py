#!/usr/bin/env python3
# Copyright (c) 2026 Mike J Hebblethwaite. See LICENSE.md.
"""perch_tech: a reverse engineer's known rules, as code. Right answers from the published codes, and nothing made up from
things that are not codes (a value never invented, a plain LED never called addressable)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import perch_tech as t

fails, n = [], 0
def check(name, got, want):
    global n; n += 1
    ok = got == want
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  got {got!r}, want {want!r}"))
    if not ok: fails.append(name)

check("cap 104 = 100 nF", t.cap_code("104"), "100 nF")
check("cap 471 = 470 pF", t.cap_code("471"), "470 pF")
check("cap 105 = 1 µF", t.cap_code("105"), "1 µF")
check("cap 1R5 = 1.5 pF", t.cap_code("1R5"), "1.5 pF")
check("trick: 999 is not a capacitor code", t.cap_code("999"), None)
check("trick: words are not a code", t.cap_code("RED"), None)
check("res 472 = 4.7 kΩ", t.res_code("472"), "4.7 kΩ")
check("res 1002 = 10 kΩ", t.res_code("1002"), "10 kΩ")
check("res 4R7 = 4.7 Ω", t.res_code("4R7"), "4.7 Ω")
check("res R47 = 0.47 Ω", t.res_code("R47"), "0.47 Ω")
check("res 000 = a link", t.res_code("000"), "0 Ω (a link)")
check("trick: 2A is not a resistor code", t.res_code("2A"), None)
check("bands brown black red gold = 1 kΩ ±5%", t.colour_bands(["brown", "black", "red", "gold"]), "1 kΩ ±5%")
check("bands yellow violet orange gold = 47 kΩ ±5%", t.colour_bands(["Yellow", "violet", "orange", "gold"]), "47 kΩ ±5%")
check("5 bands brown black black red brown = 10 kΩ ±1%", t.colour_bands(["brown", "black", "black", "red", "brown"]), "10 kΩ ±1%")
check("bands with gold multiplier: 4.7 Ω", t.colour_bands(["yellow", "violet", "gold", "gold"]), "4.7 Ω ±5%")
check("trick: a colour that is not a band", t.colour_bands(["brown", "pink", "red", "gold"]), None)
check("trick: three bands are not a value", t.colour_bands(["brown", "black", "red"]), None)
check("date 2314 = probably 2023 week 14", t.date_code("2314"), "probably 2023, week 14")
check("trick: week 99 is not a date", t.date_code("2399"), None)
check("package SOIC-8: 8 pins", t.package("NE555 SOIC-8")[:2], ("SOIC", 8))
check("package TO-92: 3 pins", t.package("BC547 in TO92")[:2], ("TO-92", 3))
check("honest: no package named, none said", t.package("an LED drawing"), None)

facts = lambda labels: [f["fact"].split(":")[0] for f in t.led_rules(labels)]
rgb = facts(["Common", "Red", "Green", "Blue"])
check("Common/Red/Green/Blue: a plain RGB LED", rgb[0].startswith("a plain RGB LED"), True)
check("...and it says NOT addressable", "NOT addressable" in t.led_rules(["Common", "Red", "Green", "Blue"])[0]["fact"], True)
check("...anode or cathode not shown: said, with how to test", rgb[1], "common anode or common cathode is not shown")
check("common anode labelled: said so", t.led_rules(["Common anode", "R", "G", "B"])[1]["fact"], "common anode (the shared pin goes to +)")
check("DIN/DOUT/VDD/GND: addressable", t.led_rules(["VDD", "DOUT", "GND", "DIN"])[0]["fact"].startswith("an addressable LED"), True)
check("honest: a regulator's pins are not an LED", t.led_rules(["VIN", "GND", "VOUT"]), [])
check("trick: only some colours is not an RGB LED", t.led_rules(["Common", "Red", "Green"]), [])

mled = {"what": "RGB LED pin-out", "part_numbers": [], "pins": [{"pin": "1", "label": "Red"}, {"pin": "2", "label": "Common"},
        {"pin": "3", "label": "Green"}, {"pin": "4", "label": "Blue"}], "values": [], "dimensions": [], "notes": "", "sure": 8}
d = t.derive(mled, ["Common", "Red", "Green", "Blue", "1", "2", "3", "4"])
check("derive on Mike's LED drawing: RGB, not addressable", any("NOT addressable" in f["fact"] for f in d), True)
check("every derived fact says how and how sure (1-11)", all(f["how"] and 1 <= f["sure"] <= 11 for f in d), True)
chip = {"what": "timer chip", "part_numbers": ["NE555"], "pins": [{"pin": str(i), "label": None} for i in range(1, 7)],
        "values": [{"name": "C1", "value": "104", "unit": None}, {"name": "R2", "value": "472", "unit": None},
                   {"name": "supply", "value": "5", "unit": "V"}], "dimensions": [], "notes": "SOIC-8, bottom view", "sure": 7}
d = [f["fact"] for f in t.derive(chip, ["NE555", "bottom view"])]
check("package and pin-count check: 6 pins read for an 8-pin part", any("lists 6 pins but a SOIC has 8" in f for f in d), True)
check("C1 104 decoded", any(f.startswith("C1: code 104 = 100 nF") for f in d), True)
check("R2 472 decoded", any(f.startswith("R2: code 472 = 4.7 kΩ") for f in d), True)
check("honest: a value with a unit is left as written", any("supply" in f for f in d), False)
check("bottom view warned", any("BOTTOM view" in f for f in d), True)
check("a failed reading derives nothing", t.derive({"error": "x"}), [])

print(f"\n{n - len(fails)} passed, {len(fails)} failed")
sys.exit(1 if fails else 0)
