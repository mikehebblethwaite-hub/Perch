# coop-perch

The bot's mind's eye (design: ~/the notes/perch.md). Version 0.01.

- `coop-perch-display` : display :2, a real X screen (Xvfb, DAMAGE/XFIXES/MIT-SHM, local socket only). Any app on it thinks it is a monitor.
- `coop-perch-eye` (C) : the local eye. DAMAGE says what changed; only touched cells of a 16x10 grid are read (shared memory) and turned
  into raw vectors (R,G,B,luma). A cell that moved is a strike; the eye zooms in (quadtree) to pin the change to a small box.
  Cells on a steady beat (clock, cursor) are tuned out; a broken beat counts again.
  Out (tape/): `vectors.bin` raw floats, local only, no tokens; `tape.txt` one short line per strike; `stats.txt` CPU and tokens
  (text tape vs a screenshot per strike vs a screenshot per second, images at w*h/750 tokens); `now.json` for the usage page live panel.
- `coop-perch-feed-two | coop-perch-feed` : build-2's memory once a second, drawn on :2 as raw blocks (used red, cache blue, free dark)
  plus the 16 biggest processes as bars and a ticking square (habit test).
- `coop-perch-test [SECONDS]` : all of the above, then prints stats and the tape.

Tape line: `HH:MM:SS strike N: col,row@x,y WxH ...` (cell, then the zoomed box in screen pixels).

Build: `make` (needs libx11-dev libxdamage-dev libxext-dev libxfixes-dev).
