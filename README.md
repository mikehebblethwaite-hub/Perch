# Perch: The Bot's Eye on the Display

Perch is an open-source tool that lets bots and automated systems see your screen in real-time, the way you do. Instead of taking screenshots and burning through tokens on OCR, Perch acts as a virtual display adaptor, capturing what the system draws as it draws it—delivering text as text, images as images, all with minimal overhead.

## Why Perch?

**The Problem:** Bots working with screens have to screenshot everything, then burn tokens on vision models to parse pixels back into meaning. It's slow, expensive, and prone to errors.

**The Solution:** Be the screen itself. Perch intercepts the display stream before it reaches the monitor, so you get direct access to:
- Text and text regions (no OCR needed)
- Dirty rectangles (only what changed)
- Input coordination (keyboard and mouse, with proper queuing)
- Low latency, minimal token cost

## What works today

- **Linux desktop apps:** Perch is the display (`:2`). It sees what changed and reads the apps' own words (AT-SPI).
- **Virtual machines (Linux or Windows guests):** Perch is the VM's only screen. QEMU hands it just the changed rectangles, so
  nothing is captured. A small helper inside the guest sends the apps' own words: AT-SPI on Linux, UI Automation on Windows.
  Perch's hands type and click through QEMU's own keyboard and mouse.
- **Web pages:** `coop-perch-web` gives a page as words and numbered actions (`click 15`), then only what changed.
- **Apps that give no words** (games, canvas drawing, remote desktops, old or custom-drawn programs) are read from their
  pixels on the machine itself (Tesseract: `sudo apt install tesseract-ocr`) and sent as `R` lines: no tokens, and only what
  changed. In a VM (`coop-perch-vmlook`): the parts that changed without words. On a Linux display (`coop-perch-look`): the
  windows that give no words, read again only when their pixels change.
- Not yet: macOS; Windows or Linux desktops without a VM.

## Try it (Ubuntu 24.04 or similar)

Copy each block into a terminal, one at a time.

**1. Get it and build it**
```bash
sudo apt install build-essential libx11-dev libxdamage-dev libxext-dev libxfixes-dev libglib2.0-dev \
     xvfb x11-utils python3-pyatspi gir1.2-atspi-2.0 gir1.2-webkit2-4.1 qemu-system-x86 tesseract-ocr
git clone https://github.com/mikehebblethwaite-hub/Perch.git
cd Perch
make
```

**2. A first look (one minute)**
```bash
./coop-perch-test 60
```
It draws your machine's memory on Perch's display and prints what the eye saw and what it cost.

**3. The web for bots**
```bash
./coop-perch-web mytest go https://example.org
./coop-perch-web mytest stop
```

**4. The demo: a screenshot bot against a Perch bot** (needs [Ollama](https://ollama.com) with a model that can see
pictures, and Google Chrome for the screenshot bot)
```bash
ollama pull gemma3:4b
PERCH_MODEL=gemma3:4b demo/coop-perch-demo run
```

**5. Send us your result**
```bash
./coop-perch-report
```
It prints a short report (nothing personal) and the page to paste it into. Every report helps, slow machines too.

**The Windows demo** needs a Windows 11 VM run by Perch: see `vm-win/coop-perch-winvm` (it builds one hands-off from
Microsoft's ISO), then `coop-perch-winvm guest` to put the helper in, and `demo/coop-perch-demo-win`.

## Use it with Claude Code

Perch comes with a Claude Code skill (`.claude/skills/perch/SKILL.md`). Open Claude Code in the Perch folder and type `/perch`,
or just ask it to look at a page or a screen: it reads Perch's lines instead of taking screenshots.

## Files

- `coop-perch-display`, `coop-perch-tap.c`, `coop-perch-eye.c`: the display, the tap on its data line, the eye
- `coop-perch-vm.c`, `coop-perch-vmrun`: a VM whose only screen is Perch; `coop-perch-act` (hands), `coop-perch-vmlook` (a bot's look)
- `coop-perch-guest` (Linux) and `coop-perch-guest.ps1` (Windows): the helper inside a guest
- `coop-perch-web`: the web for bots; `coop-perch-look`: a bot's look at a Linux display
- `coop_perch_read.py`: reading words from pixels where an app gives none
- `demo/`: the demos; `coop-perch-report`: your result as a report
- `vm-win/`: the Windows test VM

## Help make it better

- **Tested it?** Run `./coop-perch-report` and paste it into a [test report](https://github.com/mikehebblethwaite-hub/Perch/issues/new?template=test-report.yml).
- **Something broke?** Tell us in [Something went wrong](https://github.com/mikehebblethwaite-hub/Perch/issues/new?template=problem.yml).
- **Want to change the code?** See [CONTRIBUTING.md](CONTRIBUTING.md). It is short.

## Support Perch

If Perch saves you money, time, or tokens—consider supporting the project:

- **[Ko-fi](https://ko-fi.com/mikehebblethwaite)** — One-time or recurring donations
- **[GitHub Sponsors](https://github.com/sponsors/mikehebblethwaite)** — Recurring sponsorship

Every contribution helps keep Perch free and open for everyone.

## License

Free under the GNU AGPL-3.0 (if you share it or run it as a service, your source must be open too), or under a paid commercial licence for closed products. See LICENSE.md; contact mike.hebblethwaite@gmail.com.

---

Perch © 2026 Mike J Hebblethwaite
Contact: mike.hebblethwaite@gmail.com
