# coop-perch

The bot's mind's eye (design: ~/the notes/perch.md). Version 0.06.

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

Build: `make` (needs libx11-dev libxdamage-dev libxext-dev libxfixes-dev).

## The chip test (0.04)

`sudo ./coop-perch-chip -c` reads the video card's own RAM straight from the driver (amdgpu debugfs `amdgpu_vram`; on the host the
AMD Barcelo APU's 512 MB slice of main memory), finds the panel clock by timing (blocks that change with the seconds), then drops
root and shows a 32x32 box on the screen: 1024 bits of that RAM, every bit an empty white circle (the template), a blue dot when 1.
A row of 4 bytes is one pixel, so the box is 32 pixels. It follows the second hand: when the watched 128 bytes go quiet and another
part of the clock's stretch of RAM changes, it jumps there. Beside it, "How a bot sees this": the same 32 pixels as the numbers a
bot gets (R G B, changed), also written to the box of bot vectors as plane `chip` (u8, changed as bits). `-a ADDRESS` watches a fixed spot instead.
