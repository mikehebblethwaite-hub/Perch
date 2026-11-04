Perch: an app on a display (Perch's is :2; the desktop usually :0)
Look: `./perch-look screen -d :2`, then `changes -d :2` (only what changed). Start apps with `./perch-run APP` so the words they draw come as exact D lines.
Lines: `W box app 'title'` window · `F role 'name' box` focus · `t box 'words'` · `b box role 'label' [on|off]` · `i box role 'name' 'value'` · `p box role 'name'` picture · `D box 'words'` drawn by the app (exact) · `X box 'title'` a window with no words, then `R box 'text'` read from its pixels (may hold mistakes: check before acting).
Pixels of one part only: `perch-look pixels x,y,w,h -d :2` -> Read the FILE it names.
