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
- **Web pages:** `perch-web` gives a page as words and numbered actions (`click 15`), then only what changed.
- **Apps that give no words** (games, canvas drawing, remote desktops, old or custom-drawn programs) are read from their
  pixels on the machine itself (Tesseract: `sudo apt install tesseract-ocr`) and sent as `R` lines: no tokens, and only what
  changed. In a VM (`perch-vmlook`): the parts that changed without words. On a Linux display (`perch-look`): the
  windows that give no words, read again only when their pixels change.
- **Words caught where they are drawn, before they become pixels:** text a web page draws on a `<canvas>` comes as words
  (Perch notes each `fillText`/`strokeText`); an app started on Perch's display with `./perch-run APP` gives the words it
  draws itself (Pango/Cairo, HarfBuzz) as `D` lines: exact, live, and faster than reading pixels. Reading pixels (`R`) is
  left for what has no words anywhere before the screen: remote desktops, video, games that draw their own letters.
- Not yet: macOS; Windows or Linux desktops without a VM.

## Video, the bot's way

`./perch-video FILE|URL` plays a video as what it carries, not as pictures at x frames a second: its chapters, its subtitle
words, scene cuts, the words on screen at each scene (read here), what moved from where to where, and with `--describe` one
line per scene from your local vision model (Ollama). A 12-second course video: 410 characters for the bot, where one
screenshot a second would be about 16,000 tokens. `--live` paces the lines at the video's own speed; `--frame T` gives one
small picture when the answer really is in the picture.

Its sound comes as a few numbers when it changes (`s` lines; `./perch-sound FILE` for any sound file), the way a ZX81 tape
held a program in a few kilobytes: loudness 1-11; quiet, a tone and its pitch, voice?, music?, noise or a bang; and five bands
1-11 from low to high. A test file of silence, two tones, hiss, one knock and a short tune: every sound named and timed
(the knock at 6.5 s exactly). Not yet: speech as words (a local Whisper) and sounds by name (a small sound tagger).

## How a look answers (your settings)

Every look (web pages, the display, VMs) answers in one of four ways:

| Mode | The bot gets | Best for |
|---|---|---|
| `auto` (the default) | words; a small picture instead when the words would cost more | most things |
| `words` | the screen's own words and numbered actions, each line of a whole look cut short | reading and doing |
| `ocr` | words read from the pixels here on the machine | apps that give no words |
| `picture` | a zoomed-out picture of the screen: the same price however busy it is | layout, busy pages |

A whole look cuts long lines (60 characters unless you choose otherwise); `read N` gives one line whole, `url N` a link's
whole address, `read all` everything. A line just below the one above it, with the same left edge and size, writes `v`
for its box. Changes after an action always come whole.

Choose once for this machine:
```bash
./perch-settings page              # a settings page on this machine only, with a live preview and its cost
./perch-settings set mode words    # or: set cut 80 · set zoom 35 · reset · (nothing: what is set now)
```
Or for one call: `--mode picture --zoom 35` on `perch-web`, `perch-look` and `perch-vmlook`, or `look_as`, `cut`
and `zoom` in the MCP tools. Saved in `~/.config/perch/settings.json`.

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
./perch-test 60
```
It draws your machine's memory on Perch's display and prints what the eye saw and what it cost.

**3. The web for bots**
```bash
./perch-web mytest go https://example.org
./perch-web mytest stop
```

**4. The demo: a screenshot bot against a Perch bot** (needs [Ollama](https://ollama.com) with a model that can see
pictures, and Google Chrome for the screenshot bot)
```bash
ollama pull gemma3:4b
PERCH_MODEL=gemma3:4b demo/perch-demo run
```

**5. Send us your result**
```bash
./perch-report
```
It prints a short report (nothing personal) and the page to paste it into. Every report helps, slow machines too.

**The Windows demo** needs a Windows 11 VM run by Perch: see `vm-win/perch-winvm` (it builds one hands-off from
Microsoft's ISO), then `perch-winvm guest` to put the helper in, and `demo/perch-demo-win`.

## Use it with Claude Code (or any MCP app)

Install Perch as a plugin (Claude Code 2.1.275 or later), then build it once in the plugin's folder (`make`) for the display and VM eyes:

```
/plugin install perch --marketplace mikehebblethwaite-hub/Perch
```

It brings a skill (`/perch`) and an MCP server, `perch-mcp`, with the tools perch_web, perch_display, perch_vm_look,
perch_vm_read, perch_vm_act, perch_pixels and perch_status. The server runs on your machine only (stdin/stdout, no network,
no account). Any other MCP app can use it too: point it at `perch-mcp`. Inside a clone of this repo, Claude Code also
finds the skill in `.claude/skills/perch`.

## Files

- `perch-display`, `perch-tap.c`, `perch-eye.c`: the display, the tap on its data line, the eye
- `perch-vm.c`, `perch-vmrun`: a VM whose only screen is Perch; `perch-act` (hands), `perch-vmlook` (a bot's look)
- `perch-guest` (Linux) and `perch-guest.ps1` (Windows): the helper inside a guest
- `perch-web`: the web for bots; `perch-look`: a bot's look at a Linux display
- `perch-settings`, `perch_settings.py`: how a look answers (mode, line cut, picture size)
- `perch_read.py`: reading words from pixels where an app gives none
- `perch-text.c` (`libperch-text.so`), `perch-run`: the words an app draws, caught at the drawing call
- `perch-video`: a video player for bots (timed lines, not frames)
- `demo/`: the demos; `perch-report`: your result as a report
- `vm-win/`: the Windows test VM
- `site/`: the Perch website (`python3 site/build.py`), with the measured runs it shows in `site/data/`

## Help make it better

- **Tested it?** Run `./perch-report` and paste it into a [test report](https://github.com/mikehebblethwaite-hub/Perch/issues/new?template=test-report.yml).
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
