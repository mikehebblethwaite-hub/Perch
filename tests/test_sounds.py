#!/usr/bin/env python3
# Copyright (c) 2026 Mike J Hebblethwaite. See LICENSE.md.
"""perch_sounds keeps the host's rules (tests against a local web server, never the real host). Honest: an open host gives its
files. Tricks: a host that refuses is asked ONCE and the run stops (exit as Refused); a refusal mid-run stops every download at
once and is logged with its reason; a full byte budget holds downloads back; the budget is remembered by the next run; only
CC0 and exactly CC BY sounds are taken."""
import http.server, json, os, shutil, sys, tempfile, threading, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import perch_sounds as S

fails, n = [], 0
def check(name, got, want):
    global n; n += 1
    ok = got == want
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  got {got!r}, want {want!r}"))
    if not ok: fails.append(name)

STATE = {"refuse_from": 10 ** 9, "served": 0, "asked": 0}
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        STATE["asked"] += 1
        if STATE["asked"] > STATE["refuse_from"]:
            self.send_response(403); self.end_headers(); return
        body = b"x" * 1000
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        STATE["served"] += 1
    def log_message(self, *a): pass
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H); port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{port}"

def fake_records(n_rec, lic="http://creativecommons.org/licenses/by/4.0/"):
    def gen(country, state=None, taxon=None):
        for i in range(n_rec):
            yield {"gbifID": str(1000 + i), "media": [{"type": "Sound", "identifier": f"{BASE}/s{i}.mp3", "format": "audio/mpeg", "license": lic}],
                   "eventDate": None}
    return gen

d = tempfile.mkdtemp(prefix="perch-sounds-")
try:
    S.TAXA = {"birds": 212}
    S.records = fake_records(20)
    st = S.fetch(os.path.join(d, "a"), ["GB"])
    check("honest: an open host gives its 20 files", st["jobs"]["GB/birds"]["got"], 20)

    STATE.update(asked=0, refuse_from=0)
    try:
        S.fetch(os.path.join(d, "a"), ["GB"]); stopped = False
    except S.Refused:
        stopped = True
    check("trick: a refusing host is asked ONCE, then the run stops", (stopped, STATE["asked"]), (True, 1))

    STATE.update(asked=0, refuse_from=8)
    S.records = fake_records(60)
    try:
        S.fetch(os.path.join(d, "b"), ["GB"]); stopped = False
    except S.Refused:
        stopped = True
    asked_at_stop = STATE["asked"]; time.sleep(0.5)
    fl = [json.loads(l) for l in open(os.path.join(d, "b", "failures.jsonl"))]
    check("trick: a refusal mid-run stops the run", stopped, True)
    check("...with few requests after it (no retrying into the block)", asked_at_stop <= 8 + 4 + 1, True)
    check("...and the refusal is logged with its reason", any("refused, run stopped" in f["why"] for f in fl), True)

    STATE.update(asked=0, refuse_from=10 ** 9)
    S.HOUR_BYTES = 3500                                       # a budget of 3.5 files an hour
    S.records = fake_records(10)
    ev = threading.Event(); got = {}
    def run():
        try: got["st"] = S.fetch(os.path.join(d, "c"), ["GB"])
        except Exception as e: got["e"] = e
    th = threading.Thread(target=run, daemon=True); th.start(); time.sleep(4)
    check("the byte budget holds downloads back (4 files then waits)", STATE["served"] - 20 - 8 <= 5 and len(open(os.path.join(d, "c", "budget.jsonl")).readlines()) == 4, True)
    b2 = S.Budget(os.path.join(d, "c", "budget.jsonl"))
    check("...and the next run remembers what was taken", b2._used(time.time(), 3600), 4000)
    S.HOUR_BYTES = 4_000_000_000

    check("only CC0 and exactly CC BY are taken", [S.open_licence(u) for u in ("http://creativecommons.org/publicdomain/zero/1.0/",
          "http://creativecommons.org/licenses/by/4.0/", "http://creativecommons.org/licenses/by-nc/4.0/", "http://creativecommons.org/licenses/by-sa/4.0/")],
          [True, True, False, False])
finally:
    srv.shutdown(); shutil.rmtree(d, ignore_errors=True)

print(f"{n - len(fails)}/{n} pass")
sys.stdout.flush(); os._exit(1 if fails else 0)   # (the budget test leaves a waiting thread: exit without waiting for it)
