#!/usr/bin/env python3
# Copyright (c) 2026 Mike J Hebblethwaite. See LICENSE.md.
"""perch_describe teach / taught_for: what a person who knows says about a picture is bound to that exact picture by its
hash. Tricks: a different picture with the same name gets nothing; an empty teach is refused. Honest: a renamed copy of the
same picture still matches; an already-described picture gets the teaching added without losing its look."""
import json, os, shutil, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import perch_describe as pd

fails, n = [], 0
def check(name, got, want):
    global n; n += 1
    ok = got == want
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  got {got!r}, want {want!r}"))
    if not ok: fails.append(name)

d = tempfile.mkdtemp(prefix="perch-taught-")
try:
    pics, out = os.path.join(d, "pics"), os.path.join(d, "out")
    os.makedirs(pics); os.makedirs(out)
    a = os.path.join(pics, "stones.jpg"); open(a, "wb").write(b"picture A bytes")
    info = {"summary": "ancient stone ruins under a sky with several moons", "sure": 10}
    json.dump(info, open(os.path.join(out, "stones.jpg.info.json"), "w"))

    row = pd.teach(a, out, what="Stonehenge", maker="Stephen Sandner", who="Mike")
    check("taught row says how: user", (row["how"], row["from"], row["is"], row["by"]), ("user", "Mike", "Stonehenge", "Stephen Sandner"))
    got = json.load(open(os.path.join(out, "stones.jpg.info.json")))
    check("described picture gets it added", got["taught"][0]["is"], "Stonehenge")
    check("...and keeps its own look", got["summary"], info["summary"])
    check("...no hash leaks into the info", "sha256" in got["taught"][0], False)

    copy = os.path.join(d, "renamed.jpg"); shutil.copy(a, copy)
    check("honest: a renamed copy of the same picture still matches", [t["is"] for t in pd.taught_for(copy, out)], ["Stonehenge"])

    b = os.path.join(d, "stones.jpg"); open(b, "wb").write(b"a different picture")
    check("trick: a different picture with the same name gets nothing", pd.taught_for(b, out), [])

    try:
        pd.teach(a, out); refused = False
    except ValueError:
        refused = True
    check("trick: teaching nothing is refused", refused, True)

    pd.teach(a, out, note="used to be Mike's desktop picture", who="Mike")
    check("two teachings kept in order", [t.get("is") or t.get("note") for t in pd.taught_for(a, out)],
          ["Stonehenge", "used to be Mike's desktop picture"])
    check("no taught file: nothing", pd.taught_for(a, d), [])

    # poisoning: a line written by another account (or typed "from": "Mike") does not count
    tf = os.path.join(out, pd.TAUGHT)
    with open(tf, "a") as f:
        f.write(json.dumps({"sha256": pd.sha256_of(a), "how": "user", "from": "Mike", "account": "stranger", "is": "a car park"}) + "\n")
    check("trick: another account's teaching is ignored even if it says from Mike",
          "a car park" in [t.get("is") for t in pd.taught_for(a, out)], False)
    with open(tf, "a") as f:
        f.write(json.dumps({"sha256": pd.sha256_of(a), "how": "user", "from": "Mike", "is": "no account"}) + "\n")
    check("trick: a teaching with no account is ignored", "no account" in [t.get("is") for t in pd.taught_for(a, out)], False)
    os.environ["PERCH_TEACHERS"] = "stranger"
    check("PERCH_TEACHERS names who may teach", [t.get("is") for t in pd.taught_for(a, out)], ["a car park"])
    del os.environ["PERCH_TEACHERS"]
    check("the taught file is not writable by others", oct(os.stat(tf).st_mode & 0o777), "0o644")
finally:
    shutil.rmtree(d)

print(f"\n{n - len(fails)} passed, {len(fails)} failed")
sys.exit(1 if fails else 0)
