# coop-perch

The bot's mind's eye (design and decisions: NOTES.md; progress: PROGRESS.md; fails: FAILS.md). Version 0.10.

- `coop-perch-display` : display :2. Apps connect to :2; `coop-perch-tap` sits there on the data line and passes every byte to the real
  X screen, Xvfb :12 (DAMAGE/XFIXES/MIT-SHM, local socket only). Any app on it thinks it is a monitor.
- `coop-perch-tap` (C) : the interpreter on the data line. Reads every request as it goes by: keeps the window tree and drawing colours,
  and turns each drawing (fill, outline, line, image, copy, text, map, move) into one raw event: app pid, kind, screen box, colour,
  on screen or not, and the text itself as char codes. Nothing on the line = nothing written, the tap sleeps.
- **The box of bot vectors** (`coop-perch-box.h`): shared memory /dev/shm/coop-perch, one file per plane, a 4096-byte header that says
  what the numbers mean, then a ring of frames (double time + values of the plane's type: u8 u16 u32 i32 f32, the smallest that holds
  its numbers exactly; flags are packed bits). Bots mmap it and read in place; they sleep on the header's
  `wake` word (futex) until a frame lands: that word is the "data or not" pin. Planes: `draw` (i32 x 26 per event, text packed as bytes),
  `line` (u32 x 259 per second with traffic: request counts by kind, bytes in/out, apps), `eye` (u16, rows x cols x 9: R G B luma
  strike zoom box in pixels), `chip` (u8: 32 pixels R G B + 32 changed bits).
- `coop-perch-box info | raw PLANE [N] | stream PLANE | check PLANE SECONDS` : reader for tests and for bots that only take a pipe.
- `coop-perch-eye` (C) : the local eye. DAMAGE on each top-level window (root damage is dropped when a compositor floods it with
  full-screen repaints); one shared-memory read per look, a row memcmp finds the tight changed box (GPU apps damage their whole
  window every frame); each cell of a 16x10 grid keeps its
  raw vector (R,G,B,luma) up to date by swapping the box's old colours for new. A real change is a strike; the eye zooms in (quadtree) to pin the change to a small box.
  Cells on a steady beat (clock, cursor) are tuned out; a broken beat counts again.
  Out (tape/): `vectors.bin` raw floats, local only, no tokens; `tape.txt` one short line per strike; `stats.txt` CPU and tokens
  (text tape vs a screenshot per strike vs a screenshot per second, images at w*h/750 tokens); `now.json` for the usage page live panel.
- `coop-perch-feed-two | coop-perch-feed` : build-2's memory once a second, drawn on :2 as raw blocks (used red, cache blue, free dark)
  plus the 16 biggest processes as bars and a ticking square (habit test).
- `coop-perch-test [SECONDS]` : all of the above, then prints stats and the tape.

Tape line: `HH:MM:SS strike N: col,row@x,y WxH ...` (cell, then the zoomed box in screen pixels).

Build: `make` (needs libx11-dev libxdamage-dev libxext-dev libxfixes-dev). The text layer (coop-perch-guest) needs python3-pyatspi + gir1.2-atspi-2.0 (a dependency of the .deb).

## The chip test (0.04)

`sudo ./coop-perch-chip` is the Perch eye on the video card's own RAM (amdgpu debugfs `amdgpu_vram`). It masks itself out (flashes its own window,
notes the RAM that changes exactly then), then zooms: looks at all the RAM a few times, goes into the 1/16 that changed most, repeats
(512 MB > 32 MB > 2 MB > 128 KB > 8 KB > 512 B) down to 128 bytes, and zooms back out after 3 s quiet. The box: 1024 bits, every bit an empty white circle (the template), a blue dot when 1; the window from-to in fixed columns on top.
bot gets (R G B, changed), also written to the box of bot vectors as plane `chip` (u8, changed as bits). `-a ADDRESS` watches a fixed spot instead.

## The VM eye (0.08): Perch as a virtual machine's monitor

`coop-perch-vmrun DISK [NAME]` runs a guest whose only monitor is Perch: a private bus (this user only), QEMU `-display dbus` on it
(no window, no VNC, nothing on the network), and `coop-perch-vm` registered as the monitor on a private peer-to-peer line.
QEMU hands over the whole screen once, then **only the rectangles the guest changed, with their pixels**. The hypervisor already
knows what changed, so there is no capture step and no screenshot: the VM eye does Perch's part only.

- Plane `vm-NAME` in the box (same shape as `eye`: u16 rows x cols x 9), tape in `tape/vm-NAME/` (tape.txt, vectors.bin, stats.txt, now.json).
- The guest's mode changes (BIOS 640x480 -> text 720x400 -> desktop) are followed from the console's Width/Height on the bus: QEMU 8.2
  sends no new Scanout for them.
- `coop-perch-vmkeys NAME TEXT` types into the guest (QMP send-key); `--shot FILE` (QEMU's own screendump) and `coop-perch-vm -s FILE`
  + SIGUSR1 (the eye's own copy) are for tests only, to check the eye's copy against the guest's real screen.
- **The core** (`coop-perch-core.h`): cells, strikes, zoom, habits, plane, tape and stats are shared by `coop-perch-eye` (desktop, X) and
  `coop-perch-vm`. An eye only says where the screen changed and keeps the pixels there current.

Build needs libglib2.0-dev as well. Test guest (not in git, not backed up): `vm/` (Ubuntu 24.04 cloud image from build-2's cache,
an overlay disk, autologin seed).

## 0.09: meaning, hands, regions, package

- **Region lines**: cells that strike together and touch are one entry `c0,r0-c1,r1@x,y wxh`. `top` repainting a text screen: 24,522 -> 1,618 tokens / 100 s.
- **The meaning layer** (`coop-perch-guest`, inside the guest): AT-SPI, per widget; when it goes quiet (300 ms, 1.5 s max) the text that is there
  now is read from the widget and sent once (long text: several lines of 200 characters, in order, never cut): `T app x,y wxh +'text'`, `F app role 'name' box` (focus), `W app 'title' box` (window).
  Out over virtio-serial `org.coop.perch.0` (no network). The host parses every field and writes the line itself (caps: 1 KB a line,
  50 lines/s); plane `vmtext-NAME` (i32 x 24).
- **Hands**: `coop-perch-act NAME 'type ..' 'key ctrl+s' 'click X Y' 'scroll X Y down 3' 'move X Y'` -> QEMU's own keyboard/mouse (usb-tablet).
  Events go out from a queue on a timer (unpaced, 64 a tick), so the eye keeps seeing while a bot types; replies in order; `A` lines on the tape.
  Measured with QEMU's own keyboard trace: 640 characters unpaced in 0.3-0.5 s, screen quiet or busy: every key read by the guest, text exact.
- `coop-perch-wait -p vm-NAME`: wait for a VM screen to settle.
- **Package**: `make deb` -> `dist/coop-perch_VERSION_amd64.deb` (stripped binaries, scripts, guest helper, QUICKSTART.txt; no source).
- Bot job (9 steps: type in an editor, run a command in a terminal): ~323 tokens of tape (847 when typing was paced) vs ~12,285 for a screenshot after each step;
  the text came back exact; the eye's copy of the 1280x800 screen = QEMU's, 0 pixels apart; 1.9% of a core.

## coop-perch-web (0.10): the web for bots

No Chrome, no Firefox, no window: a web engine (WebKitGTK, ephemeral, nothing on disk) runs off-screen on a private display only to run
the page's scripts and lay it out. The bot gets lines (`t` words, `a N` links with their address, `b N` buttons, `i N` inputs, `s N`
lists, each with its box) and acts by number (`click N`, `type N TEXT`, `enter N`); after every action only the changes (`+ - ~`).
The reader runs in its own script world (out of the page's reach); page text is always quoted data. Decoration (no letter or digit)
is left out. A live website: the page ~550 tokens (a screenshot ~1,365); a search job (open, type, search) ~1,050 vs ~5,460.

Copyright (c) 2026 Mike J Hebblethwaite. All rights reserved. See COPYRIGHT.
