Perch: a VM (NAME) or the Windows 11 VM (NAME win)
Windows up? `./vm-win/perch-winvm status`; if not: `PERCH_WIN_NET=off ./vm-win/perch-winvm perch &` (words come ~1 min after sign-in).
Look: `./perch-vmlook NAME` (lines since your last look) or `... NAME window` (the window in front); `... NAME read x,y,w,h` reads a part from pixels.
Hands: `./perch-act NAME 'click X Y' 'type TEXT' 'key ctrl+s'` (aim at a box's centre; UK keyboard), then `./perch-wait -t 10 -p vm-NAME` and look again.
Lines: `W app 'title' box` window · `F app role 'name' box` focus · `T app box +'words'` · `R box +'text'` read from pixels (may hold mistakes; `[name]` = a picture it knows).
Admin inside Windows: `./vm-win/perch-winvm ssh 'POWERSHELL'` (cannot see the screen). Pixels of a part: `perch-vmlook NAME pixels x,y,w,h`.
