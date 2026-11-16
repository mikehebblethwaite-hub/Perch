---
name: perch-reverse
description: Reverse-engineer a technical item from a picture the way an engineer does - a chip, a pin-out, a circuit board, a schematic, a datasheet drawing, a resistor or an LED. Use whenever a picture or Perch reading shows electronics or an engineering drawing, or someone asks what a part is, how it is wired, or what its markings mean.
---
<!-- Copyright (c) 2026 Mike J Hebblethwaite. See LICENSE.md. Perch reverse skill 0.01. -->
Read first, then reason with known rules. The wheel is made: these are published conventions, not guesses.

1. **Look with Perch.** A file: `python3 perch_describe.py FOLDER OUT` (its `kind` says `technical` and a second look reads
   pins, part numbers, values, view, pin-1 mark, bands). Its `derived` facts come from `perch_tech.py` (codes decoded by rule).
   A page or screen: the `perch` skill. Never a screenshot.
2. **Which view.** Datasheets draw TOP view unless they say BOTTOM view. Bottom view = pins run the other way round.
3. **Find pin 1:** a dot, a notch, a bevelled edge, a square pad, a stripe, or the longest leg. Count pins ANTICLOCKWISE from
   pin 1, seen from the top (DIP, SOIC, QFP, QFN). BGA: rows A, B, C... and columns 1, 2, 3..., A1 at the marked corner.
   TO-92: flat face towards you, legs down, pins 1-2-3 left to right (what each pin is depends on the part: check the datasheet).
4. **Markings on a chip:** maker logo, part number, then often a date code (YYWW: "2314" = probably 2023 week 14) and a lot
   number. Tiny parts carry a short SMD marking code instead of the part number: say the code as written; never turn it into a
   part number without a source.
5. **Passive parts:** capacitor codes (104 = 100 nF, 471 = 470 pF), resistor codes (472 = 4.7 kΩ, 4R7 = 4.7 Ω, 000 = a link),
   colour bands (brown-black-red-gold = 1 kΩ ±5%). `perch_tech.py` decodes them; trust its `how`.
6. **LEDs:** long leg = anode (+), flat edge or short leg = cathode (−). A 4-leg RGB LED with Common/R/G/B is a PLAIN RGB LED:
   one leg per colour, one shared (longest) leg, common anode or common cathode; not addressable. An ADDRESSABLE LED
   (WS2812-style) has data pins: DIN, DOUT, VDD, GND, and chains one to the next.
7. **Boards:** follow the wide traces and big planes for power and ground; small capacitors sit beside a chip's power pins; the
   regulator is near where power comes in; a crystal sits beside the processor. Test points and silkscreen labels name nets.
8. **Say how sure.** Every reading and fact: what it is, how it was worked out, sure 1-11 (never 0). Something not shown is
   "not shown", never filled in. A part number or value that is not written is never invented.
9. **Keep the design ours.** Notes on a person's own design stay on their machine (Perch never uploads).
