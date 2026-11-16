# Copyright (c) 2026 Mike J Hebblethwaite. See LICENSE.md.
# perch_where: where we are, by elimination (Mike, 16 Nov 2026: "tree yup, squirrel yup, not a desert, not a beach"). Every thing
# the branches find ("a red squirrel", "a tree", "a grey squirrel") says where it lives and where it does not; the trunk keeps the
# places that fit what was seen. Never GPS. A sighting is said with how sure it is, so one shaky sighting cannot throw out a place
# that sure sightings agree on; sightings that cannot all be true together are said as such ("one of these is probably wrong").
# Two kinds of place: countries (ISO codes, GeoNames' list) and habitats (desert, beach, woodland ...).
# Ranges: from GBIF (the Global Biodiversity Information Facility, the open world record of where each species has been seen;
# https://www.gbif.org), fetched once per species (`perch_where.py fetch`) into the place index's src/ranges, so they are kept with
# the GeoNames download. Only people's own sightings in the wild are counted (HUMAN_OBSERVATION, present). A country counts fully
# when it holds at least 0.1% of the species' records, half when it has a few (5 or more, and 1 in 100,000 of them), not at all
# for a stray one or two (a zoo, a mistake). Without a fetched range, KNOWN's seed list is used: from general knowledge, unchecked.
import json, os, time, urllib.parse, urllib.request

GEO = os.environ.get("PERCH_GEO", os.path.expanduser("~/.cache/perch-geo"))
RANGES = os.path.join(GEO, "src", "ranges")
GBIF = "https://api.gbif.org/v1"
# the names people say -> the names GBIF files them under (one or more taxa, merged)
SCIENTIFIC = {"red squirrel": ["Sciurus vulgaris"], "grey squirrel": ["Sciurus carolinensis"],
              "kangaroo": ["Macropus giganteus", "Macropus fuliginosus", "Osphranter rufus", "Osphranter robustus"], "european robin": ["Erithacus rubecula"],
              "american robin": ["Turdus migratorius"], "cactus": ["Cactaceae"]}


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "perch-where/0.01"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def fetch_range(common):
    """Ask GBIF which countries hold sightings of this thing; keep the counts (and where they came from) in RANGES."""
    counts, taxa = {}, []
    for name in SCIENTIFIC[common]:
        m = _get(f"{GBIF}/species/match?name={urllib.parse.quote(name)}")
        key = m.get("usageKey")
        if not key or m.get("matchType") != "EXACT" or m.get("rank") not in ("FAMILY", "GENUS", "SPECIES", "SUBSPECIES"):
            raise ValueError(f"GBIF has no exact {name} (got {m.get('scientificName')}, {m.get('rank')}, {m.get('matchType')})")
        taxa.append({"name": m.get("scientificName"), "key": key, "rank": m.get("rank")})
        d = _get(f"{GBIF}/occurrence/search?taxonKey={key}&basisOfRecord=HUMAN_OBSERVATION&occurrenceStatus=PRESENT"
                 "&limit=0&facet=country&facetLimit=400")
        for c in (d.get("facets") or [{}])[0].get("counts", []):
            counts[c["name"]] = counts.get(c["name"], 0) + c["count"]
    os.makedirs(RANGES, exist_ok=True)
    out = {"common": common, "taxa": taxa, "counts": counts, "total": sum(counts.values()),
           "source": "GBIF.org occurrence search (HUMAN_OBSERVATION, PRESENT), country facet",
           "fetched": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    path = os.path.join(RANGES, common.replace(" ", "_") + ".json")
    with open(path + ".new", "w") as f: json.dump(out, f, indent=1)
    os.replace(path + ".new", path)
    return out


def range_of(common):
    """{country: weight 1 or 0.5} from the fetched GBIF range, or None if not fetched (then KNOWN's seed is used)."""
    path = os.path.join(RANGES, common.replace(" ", "_") + ".json")
    if not os.path.exists(path): return None
    r = json.load(open(path))
    tot = max(1, r["total"])
    return {c: (1.0 if n >= tot * 0.001 else 0.5) for c, n in r["counts"].items() if n >= max(5, tot * 1e-5)}


HABITATS = {"woodland", "park or garden", "grassland", "farmland", "town", "desert", "beach", "open sea", "ice", "mountain"}

# what a thing says about where it is: "in" = countries it lives in (native or brought in), "habitat_in" / "habitat_out"
KNOWN = {
    "red squirrel": {"in": "GB IE FR DE IT ES PT NL BE LU CH AT DK NO SE FI PL CZ SK HU SI HR RO BG RS BA ME AL MK GR BY UA LT LV EE RU MN CN KZ KP KR JP",
                     "habitat_in": {"woodland", "park or garden"}, "habitat_out": {"desert", "beach", "open sea", "ice"},
                     "note": "native across Europe and northern Asia"},
    "grey squirrel": {"in": "US CA GB IE IT ZA", "habitat_in": {"woodland", "park or garden", "town"},
                      "habitat_out": {"desert", "beach", "open sea", "ice"},
                      "note": "native to eastern North America; brought into Britain, Ireland, Italy and South Africa"},
    "kangaroo": {"in": "AU", "habitat_in": {"grassland", "woodland", "farmland"}, "habitat_out": {"open sea", "ice", "town"}},
    "european robin": {"in": "GB IE FR DE IT ES PT NL BE LU CH AT DK NO SE FI PL CZ SK HU SI HR RO BG RS BA ME AL MK GR BY UA LT LV EE RU TR MA DZ TN",
                       "habitat_in": {"woodland", "park or garden", "farmland"}, "habitat_out": {"open sea", "ice"}},
    "american robin": {"in": "US CA MX", "habitat_in": {"woodland", "park or garden", "town", "farmland"}, "habitat_out": {"open sea", "ice"}},
    "tree": {"habitat_out": {"open sea", "ice", "beach", "desert"}, "habitat_weight": 0.5,      # palms on a beach, an oasis: half weight
             "note": "trees grow almost everywhere people live; a tree mostly says what the place is not"},
    "cactus": {"habitat_in": {"desert"}, "habitat_out": {"ice", "open sea"}, "in": "US MX AR CL PE BO BR",
               "note": "native to the Americas (brought elsewhere in gardens)"},
}


def all_countries():
    """Every country in GeoNames' list (the place index's own copy), else the countries named in KNOWN."""
    path = os.path.join(os.environ.get("PERCH_GEO", os.path.expanduser("~/.cache/perch-geo")), "src", "countryInfo.txt")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return {l.split("\t")[0]: l.split("\t")[4] for l in f if not l.startswith("#") and len(l.split("\t")) > 4}
    out = {}
    for k in KNOWN.values():
        for c in k.get("in", "").split(): out[c] = c
    return out


class Where:
    """The trunk's place reasoning: sightings in, places that still fit out."""

    def __init__(self):
        self.countries = all_countries()
        self.seen = []                                   # (what, sure, from)
        self.ranges, self.range_from = {}, {}
        for what, k in KNOWN.items():                    # GBIF's range where fetched, else the seed list (said as such)
            g = range_of(what)
            if g: self.ranges[what], self.range_from[what] = g, "GBIF"
            elif k.get("in"): self.ranges[what], self.range_from[what] = {c: 1.0 for c in k["in"].split()}, "seed (unchecked)"

    def add(self, what, sure, by=None):
        what = what.strip().lower()
        if what not in KNOWN: return False               # nothing known about it yet: it says nothing about the place
        self.seen.append((what, max(1, min(11, int(sure))), by))
        return True

    def score(self):
        """Each country and habitat gets + sure for every sighting that fits it and - sure for every sighting that rules it out."""
        cs = {c: 0.0 for c in self.countries}
        hs = {h: 0.0 for h in HABITATS}
        for what, sure, _ in self.seen:
            k = KNOWN[what]
            rng = self.ranges.get(what)
            if rng:
                for c in cs: cs[c] += sure * rng[c] if rng.get(c) else -sure
            w = k.get("habitat_weight", 1.0)
            for h in k.get("habitat_in", ()): hs[h] += sure * w
            for h in k.get("habitat_out", ()): hs[h] -= sure * w
        return cs, hs

    def answer(self):
        cs, hs = self.score()
        if not self.seen:
            return {"said": "nothing seen yet that says where we are", "fits": [], "ruled_out": []}
        top = max(cs.values()) if cs else 0
        ranged = [s for s in self.seen if self.ranges.get(s[0])]
        fits = sorted(c for c, v in cs.items() if ranged and v >= top - 0.01 and v > -sum(s[1] for s in ranged))
        all_true = [c for c in cs if ranged and all(self.ranges[s[0]].get(c) for s in ranged)]
        out_h = sorted(h for h, v in hs.items() if v < 0)
        in_h = sorted(h for h, v in hs.items() if v > 0)
        names = [self.countries.get(c, c) for c in fits]
        seen = ", ".join(f"{w} (sure {s}{', range: ' + self.range_from[w] if w in self.range_from else ''})" for w, s, _ in self.seen)
        line = f"seen: {seen}"
        if ranged:
            if all_true:
                both = sorted(all_true)
                line += f"; all of them live in {len(both)} of {len(cs)} countries; best: " + ", ".join(names[:12]) + (" ..." if len(names) > 12 else "")
            else:
                line += ("; no country has all of these together: one sighting is probably wrong; the best fit: "
                         + ", ".join(names[:12]))
        if out_h: line += f"; not {', not '.join(out_h)}"
        if in_h: line += f"; likely {' or '.join(in_h)}"
        return {"said": line, "fits": fits, "all_agree": bool(all_true), "all_live_in": sorted(all_true), "habitat_out": out_h, "habitat_in": in_h,
                "seen": [{"what": w, "sure": s, "from": b} for w, s, b in self.seen]}


if __name__ == "__main__":
    import sys
    if sys.argv[1:2] == ["fetch"]:
        for common in (sys.argv[2:] or SCIENTIFIC):
            r = fetch_range(common)
            print(f"{common}: {r['total']} sightings in {len(r['counts'])} countries; counted: {len(range_of(common))}")
        sys.exit(0)
    w = Where()
    for arg in sys.argv[1:]:                             # e.g.  "red squirrel:7" "tree:9"
        what, _, sure = arg.rpartition(":")
        w.add(what or arg, int(sure) if sure.isdigit() else 6)
    print(json.dumps(w.answer(), ensure_ascii=False, indent=1))
