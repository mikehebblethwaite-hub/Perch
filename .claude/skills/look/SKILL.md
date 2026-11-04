---
name: look
description: Look at a screen the bot way, with Perch instead of a screenshot. /look on its own looks at your display; /look URL looks at a web page; /look NAME looks at a virtual machine run by Perch (e.g. /look win). Use it whenever someone types /look or says "look at my display".
---
<!-- Copyright (c) 2026 Mike J Hebblethwaite. See LICENSE.md. Perch's /look (public). -->
Look at what the person means, through Perch (the screen's own words as short lines), never by taking a screenshot.
What to look at: $ARGUMENTS (nothing = their display).

1. Pick the eye:
   - nothing, "display", "desktop", "screen" → their display, normally :0 (Perch's own display is :2)
   - an address (http..., or a local .html file) → that web page
   - a word that names a VM (win, a guest's name) → that virtual machine
2. Look. If the perch MCP tools are there (perch_display, perch_web, perch_vm_look, perch_status), use them. Otherwise run Perch's
   tools from the Perch folder: `./perch-look screen -d :0`, `./perch-web look go URL`, `./perch-vmlook NAME window`.
   Not sure what is running? perch_status (or `ls $XDG_RUNTIME_DIR | grep perch-vm-`).
3. Answer in plain words: what is on the screen, what is in front, what needs attention. Quote words exactly when they matter.
   Lines read from pixels (R) may hold mistakes: say so if one matters. Say plainly what could not be read.
4. Only if the answer is in a picture (a photo, a chart), take that one part's pixels, never the whole screen.
Secrets: if the screen shows a password, a key, a 2FA secret or code, a token or card numbers, never repeat it or any part of it: say only that something secret is visible, where, and that it should be hidden or changed.
