# Copyright (c) 2026 Mike J Hebblethwaite
# Licensed under the GNU AGPL-3.0 or a commercial licence - see LICENSE.md
# The screenshot bot for the Perch demo: the usual way bots see a page today. Google Chrome (headless, its own throwaway profile)
# driven over Chrome's DevTools pipe (fd 3/4, no network port), a full 1280x800 PNG after every step, clicks at screen points.
import base64, json, os, subprocess, tempfile, time

W, H = 1280, 800


class Chrome:
    def __init__(self):
        self.prof = tempfile.mkdtemp(prefix="coop-perch-demo-chrome-")
        r1, w1 = os.pipe()            # we write -> chrome reads on fd 3
        r2, w2 = os.pipe()            # chrome writes on fd 4 -> we read
        def fds():
            os.dup2(r1, 3); os.dup2(w2, 4)
        self.p = subprocess.Popen(["google-chrome", "--headless=new", "--remote-debugging-pipe", f"--user-data-dir={self.prof}",
                                   f"--window-size={W},{H}", "--hide-scrollbars", "--no-first-run", "--no-default-browser-check",
                                   "--disable-extensions", "--disable-background-networking", "--disable-sync", "--mute-audio",
                                   "about:blank"], pass_fds=(3, 4), preexec_fn=fds,
                                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.close(r1); os.close(w2)
        self.w, self.r = os.fdopen(w1, "wb", 0), os.fdopen(r2, "rb", 0)
        self.n, self.buf, self.sid = 0, b"", None
        t = [x for x in self.call("Target.getTargets")["targetInfos"] if x["type"] == "page"][0]
        self.sid = self.call("Target.attachToTarget", targetId=t["targetId"], flatten=True)["sessionId"]
        self.call("Page.enable"); self.call("Runtime.enable")
        self.call("Emulation.setDeviceMetricsOverride", width=W, height=H, deviceScaleFactor=1, mobile=False)

    def call(self, method, **params):
        self.n += 1
        msg = {"id": self.n, "method": method, "params": params}
        if self.sid and not method.startswith("Target."):
            msg["sessionId"] = self.sid
        self.w.write(json.dumps(msg).encode() + b"\0")
        while True:
            while b"\0" not in self.buf:
                d = self.r.read(1 << 20)
                if not d:
                    raise RuntimeError("chrome went away")
                self.buf += d
            one, self.buf = self.buf.split(b"\0", 1)
            m = json.loads(one)
            if m.get("id") == self.n:
                if "error" in m:
                    raise RuntimeError(f"{method}: {m['error'].get('message')}")
                return m.get("result", {})

    def js(self, expr):
        return self.call("Runtime.evaluate", expression=expr, returnByValue=True).get("result", {}).get("value")

    def settle(self, quiet=0.3, most=10):
        t0, last, same = time.time(), None, 0
        while time.time() - t0 < most:
            v = self.js("document.readyState + (document.body ? document.body.innerHTML.length : 0)")
            same = same + 1 if v == last and str(v).startswith("complete") else 0
            last = v
            if same * 0.1 >= quiet:
                return
            time.sleep(0.1)

    def go(self, url):
        self.call("Page.navigate", url=url); time.sleep(0.2); self.settle()

    def click(self, x, y):
        for t in ("mousePressed", "mouseReleased"):
            self.call("Input.dispatchMouseEvent", type=t, x=x, y=y, button="left", clickCount=1)
        time.sleep(0.1); self.settle()

    def shot(self):
        """the whole window as PNG bytes, as a screenshot bot sends it"""
        return base64.b64decode(self.call("Page.captureScreenshot", format="png")["data"])

    def stop(self):
        try:
            self.call("Browser.close")
        except Exception:
            pass
        try:
            self.p.wait(5)
        except Exception:
            self.p.kill()
        subprocess.run(["rm", "-rf", self.prof])
