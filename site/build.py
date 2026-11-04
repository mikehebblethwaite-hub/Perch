#!/usr/bin/python3 -I
# Copyright (c) 2026 Mike J Hebblethwaite
# Licensed under the GNU AGPL-3.0 or a commercial licence - see LICENSE.md
# Builds the Perch site into site/out/: the pages below in one shell, the demo replay
# from ../demo/demo.html with the measured run (../tape/demo/run.json + its screenshots). Deploy: site/deploy.sh.
import html, json, os, re, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
PERCH = os.path.dirname(HERE)                         # the Perch repo (the demo page comes from demo/demo.html)
RUN = os.path.join(HERE, "data", "demo-linux")          # each measured run, kept here so the site can be built again from the repo

MARK = ('<svg viewBox="0 0 32 32" aria-hidden="true"><path d="M3 25h26" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/>'
        '<path d="M6 17c3-6 7-9 10-9s7 3 10 9c-3 4-7 6-10 6s-7-2-10-6z" fill="none" stroke="currentColor" stroke-width="2.2"/>'
        '<circle cx="16" cy="16" r="3.6" fill="var(--perch)"/></svg>')
NAV = [("home", "", "Home"), ("demo", "demo/", "Demo"), ("help", "help/", "Help"), ("download", "download/", "Download")]


def shell(title, nav, body, depth, extra_head=""):
    up = "../" * depth
    links = "".join(f'<a href="{up}{href}"{" aria-current=page" if key == nav else ""}>{text}</a>' for key, href, text in NAV)
    return f"""<!doctype html>
<!-- Copyright (c) 2026 Mike J Hebblethwaite. All rights reserved. Perch is open-source: https://github.com/mikehebblethwaite-hub/Perch -->
<html lang="en-GB"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<link rel="stylesheet" href="{up}assets/site.css">{extra_head}</head><body>
<header class="top"><div class="wrap"><a class="mark" href="{up}">{MARK}Perch</a><nav aria-label="Site">{links}</nav></div></header>
<main><div class="wrap">{body}</div></main>
<footer><div class="wrap">Perch © 2026 Mike J Hebblethwaite. <a href="https://github.com/mikehebblethwaite-hub/Perch">Open source on GitHub</a> · <a href="https://ko-fi.com/mikehebblethwaite">Support on Ko-fi</a></div></footer>
</body></html>
"""


def run_data():
    ev = json.load(open(os.path.join(RUN, "run.json")))
    for e in ev:
        if e.get("img"):
            e["img"] = re.sub(r"^/tape/(shot-\d+\.png).*$", r"shots/\1", e["img"])
    res = next(e for e in ev if e["kind"] == "done")["result"]
    return ev, res


EV, RES = run_data()
S, P = RES["shot"], RES["perch"]
X_TOK = S["claude"] / P["claude"]
X_TIME = S["wall"] / P["wall"]
E_SHARE = P["j"] / S["j"]
PERCH_SEE = [e for e in EV if e.get("lane") == "perch" and e["kind"] == "see"]
# the same job on a Windows desktop (demo/coop-perch-demo-win): its own measured run
WRUN = os.path.join(HERE, "data", "demo-win")


def read_section():
    """the reading of apps that give no words: one measured read of a canvas board on the Windows VM (data/read-test.json)"""
    try:
        d = json.load(open(os.path.join(HERE, "data", "read-test.json")))
    except (OSError, ValueError):
        return ""
    lines = "\n".join(d["lines"])
    return f'''
<h2>Even apps that give no words</h2>
<p>Some apps draw their words straight onto the screen and tell the rest of the computer nothing: games, drawings in a web page,
remote desktops, older programs. For those, Perch reads the words from the pixels itself, on your own machine, where it changed and
only where the app said nothing. It costs no tokens: the bot is sent short lines, not a picture.</p>
<div class="pair" aria-label="A board drawn as pixels only, and what the bot was sent">
  <section class="card"><h3><i style="background:var(--shot)"></i>What the screen shows</h3>
    <div class="cost">{d["crop_tokens"]:,} <small>tokens as a picture, just this part</small></div>
    <img src="read/board.png" alt="A team board and a game score drawn as pixels only, with no words behind them" width="{d["w"]}" height="{d["h"]}" loading="lazy">
    <p class="note">Drawn on a canvas in Edge on Windows: the app tells Windows no words at all.</p></section>
  <section class="card"><h3><i style="background:var(--perch)"></i>What the bot was sent</h3>
    <div class="cost">{d["bytes"]} <small>bytes, read in {d["seconds"]:.1f} s</small></div>
    <pre aria-label="The exact lines the bot was sent">{lines_html(lines)}</pre>
    <p class="note">R means read from pixels, so a bot knows it may hold a mistake. When the score went up, the next look was one line,
    <code>{html.escape(d["change_line"].split(" +", 1)[1])}</code>, in {d["change_seconds"]:.2f} s.</p></section>
</div>
<p class="note">Measured on {d["when"]}, Windows 11 in a virtual machine, read with Tesseract on the host. It works the same way for Linux apps and for Linux virtual machines.</p>
'''


def learn_section():
    """the learning reader: one measured fair test (data/learn-test.json, from demo/coop-perch-read-test on the Windows VM)"""
    try:
        d = json.load(open(os.path.join(HERE, "data", "learn-test.json")))
    except (OSError, ValueError):
        return ""
    pic = d["pictures"]
    miss = d["words"] - d["fixed"]
    return f'''
<h2>It learns as it goes, like a child</h2>
<p>Most of a screen already comes with the apps' own words, so Perch knows they are right. Now and then it also reads those same
places from the pixels and compares, the way a child learns a word by hearing it named. It learns its own mistakes on that
screen: bits of window borders and icons stuck to a line, and words it keeps reading wrong. A lesson is only used once it has been
seen more than once and is usually right. It starts with a few lessons that every screen reader needs, then its own take over.</p>
<p>It recognises things too, not only letters. When an app names something that has no letters, like a close button, Perch keeps
its look with the name. Later, when an app that gives no words shows the same thing, the bot is told <code>[Close]</code>.</p>
<div class="facts">
  <div><b>{d["fixed"] / d["words"] * 100:.1f}% of words</b>read right ({d["fixed"]} of {d["words"]}), on lines it had not learned from</div>
  <div><b>{pic["right"]} of {pic["scored"]} pictures</b>learned in one window, recognised in a different window by their look</div>
  <div><b>{d["index_ms"]:.2f} ms</b>to find a picture among {d["index_pictures"]:,} stored ones</div>
  <div><b>On your machine</b>no tokens, nothing sent anywhere</div>
</div>
<p class="note">Measured on {d["when"]} on a Windows 11 inbox: the reader learned from half of the screen's lines and was scored on the
other half, {d["rounds"]} times over. {"The " + str(miss) + " words it missed were one dot read as a dash, which came once a round: too rare to learn inside the test." if miss else ""}</p>
'''


def win_section():
    try:
        r = json.load(open(os.path.join(WRUN, "result.json")))
        ev = json.load(open(os.path.join(WRUN, "run.json")))
    except (OSError, ValueError):
        return ""
    s, p, truth = r["shot"], r["perch"], set(r["truth"])
    see = [e for e in ev if e.get("lane") == "perch" and e["kind"] == "see" and e.get("lines")]
    one = next((e for e in see if "From:" in e["lines"]), see[0] if see else None)
    def right(b):
        n = set(b["names"])
        return f"found all {len(truth)}" if n == truth else f"found {len(n & truth)} of {len(truth)}" + (f", plus {len(n - truth)} wrong" if n - truth else "")
    x_tok = s["claude"] / p["claude"]
    energy = (f'<div><b>{s["j"] / p["j"]:.1f}× less energy</b>{p["j"] / 1000:.1f} kJ against {s["j"] / 1000:.1f} kJ</div>'
              if p.get("j") and s.get("j") else "")
    look = (f'''<div class="pair" aria-label="One look at the same email on Windows, as each bot was sent it">
  <section class="card"><h3><i style="background:var(--shot)"></i>Sent as a screenshot</h3>
    <div class="cost">{r["img_tokens"]:,} <small>tokens, every look</small></div>
    <img src="demo/win/shot-1.png" alt="A screenshot of the Windows desktop with an email open in Edge" width="1024" height="768" loading="lazy">
    <p class="note">The whole Windows screen after every action, {s["bytes"] // max(1, s["looks"]) // 1000} KB on average.</p></section>
  <section class="card"><h3><i style="background:var(--perch)"></i>Sent through Perch</h3>
    <div class="cost">{one["bytes"]:,} <small>bytes for the same look</small></div>
    <pre aria-label="The exact lines the model was sent">{lines_html(one["lines"])}</pre>
    <p class="note">Windows' own words for what changed, read from the apps through Windows' accessibility layer, each with its box.</p></section>
</div>''' if one else "")
    return f'''
<h2>The same job on Windows</h2>
<p>The same inbox, open in Edge on a Windows 11 desktop that Perch is the screen for, and the same model and questions. The screenshot bot was sent a picture of the whole Windows screen after every action; the Perch bot was sent Windows' own words.</p>
<div class="facts">
  <div><b>{x_tok:.1f}× fewer</b>tokens ({p["claude"]:,} against {s["claude"]:,})</div>
  {energy}
  <div><b>{s["wall"] / p["wall"]:.1f}× quicker</b>{p["wall"] / 60:.0f} minutes against {s["wall"] / 60:.0f}</div>
  <div><b>Answers</b>Perch bot {right(p)}; screenshot bot {right(s)}</div>
</div>
{look}
<p class="note">Measured on one run, on one machine: Windows 11 in a virtual machine, the same 7.5B model on its processor. Tokens are counted as Claude bills them, with each screenshot at Claude's {r["img_tokens"]:,} tokens for a {r["screen"].split(", ")[-1]} screen. The local model itself shrinks a screenshot to {r["g_img"]:,} tokens, so by its own count the screenshot bot used {"fewer" if s["local"] < p["local"] else "more"} tokens ({s["local"]:,} against {p["local"]:,}).</p>
'''


def lines_html(text):
    out = []
    for l in text.splitlines():
        cls = "add" if l.startswith("+") else "gone" if l[:1] in "-=" else ""
        out.append(f'<span class="{cls}">{html.escape(l)}</span>' if cls else html.escape(l))
    return "\n".join(out)


PAGES = {}

PAGES["index.html"] = ("Perch: bots see screens without screenshots", "home", f"""
<h1>Your bot can see the screen without taking screenshots.</h1>
<p class="lede">Perch sits where the screen is drawn and tells a bot what is there: the words, the buttons and the boxes they sit in.
After that it sends only what changed. The bot reads exact text instead of guessing at pictures, and it spends far fewer tokens doing it.</p>
<div class="cta"><a class="btn solid" href="demo/">Watch the demo</a><a class="btn" href="help/">How it works</a></div>

<div class="pair" aria-label="One look at the same inbox, as each bot was sent it">
  <section class="card"><h3><i style="background:var(--shot)"></i>Sent as a screenshot</h3>
    <div class="cost">1,365 <small>tokens, every look</small></div>
    <img src="demo/shots/shot-1.png" alt="A screenshot of an email opened in a webmail inbox" width="1280" height="800" loading="lazy">
    <p class="note">A full 1280 × 800 picture after every action, {S["bytes"] // S["looks"] // 1000} KB on average. The model has to read the words back out of the pixels.</p></section>
  <section class="card"><h3><i style="background:var(--perch)"></i>Sent through Perch</h3>
    <div class="cost">{PERCH_SEE[1]["bytes"]} <small>bytes for the same look</small></div>
    <pre aria-label="The exact lines the model was sent">{lines_html(PERCH_SEE[1]["lines"])}</pre>
    <p class="note">The same moment: the inbox list went (<code>- a 1-14</code>, the model already has it) and the opened email came in as exact words, each with its box and a number to act on.</p></section>
</div>

<h2>Pre-launch beta, first demo</h2>
<p>One everyday job, done twice by the same local model with the same hands: go through five unread emails, say which need a reply today and draft the replies.</p>
<div class="facts">
  <div><b>{X_TOK:.1f}× fewer</b>tokens ({P["claude"]:,} against {S["claude"]:,})</div>
  <div><b>Half the energy</b>{P["j"] / 1000:.1f} kJ against {S["j"] / 1000:.1f} kJ</div>
  <div><b>{X_TIME:.1f}× quicker</b>{P["wall"] / 60:.0f} minutes against {S["wall"] / 60:.0f}</div>
  <div><b>Same answers</b>both bots found the 4 emails that need a reply</div>
</div>
<p class="note">Measured on one run, on one machine, with a 7.5B model on its processor. Tokens are counted as a chat model like Claude bills them, with each screenshot at Claude's 1,365. <a href="demo/">See the whole run</a>.</p>

{win_section()}
{read_section()}
{learn_section()}
<h2>What it does</h2>
<div class="cols">
  <div><h3>Reads the screen as data</h3><p>Pictures are made from words and boxes that already exist. Perch catches them before they become pixels, so nothing has to be turned back into text.</p></div>
  <div><h3>Sends only what changed</h3><p>After the first look, the bot gets short lines: what appeared, what went, what moved. A screen that stays still costs nothing.</p></div>
  <div><h3>Lets the bot act by number</h3><p>Every link, button and box has a number. The bot says <code>click 15</code> instead of guessing where to point on a picture.</p></div>
</div>
""")

PAGES["download/index.html"] = ("Download Perch", "download", """
<h1>Get Perch</h1>
<p class="lede">Perch is in pre-launch beta. It runs on Linux (Ubuntu 24.04 and similar) as one package.</p>
<section class="card" style="max-width:640px">
  <h3>Perch pre-launch beta licence</h3>
  <p>One licence for one machine, with every beta update. The download link arrives straight after payment.</p>
  <form id="buy" action="https://www.paypal.com/cgi-bin/webscr" method="post" data-price="">
    <input type="hidden" name="cmd" value="_xclick">
    <input type="hidden" name="business" value="paypal@ethernest.co.uk">
    <input type="hidden" name="item_name" value="Perch pre-launch beta licence">
    <input type="hidden" name="item_number" value="perch-beta">
    <input type="hidden" name="currency_code" value="GBP">
    <input type="hidden" name="amount" value="">
    <input type="hidden" name="no_shipping" value="1">
    <input type="hidden" name="return" value="https://ethernest.co.uk/download/thanks.html">
    <input type="hidden" name="cancel_return" value="https://ethernest.co.uk/download/">
    <p id="price" style="font-size:22px;font-weight:700;margin:10px 0"></p>
    <button class="btn solid" id="pay" type="submit">Pay with PayPal</button>
  </form>
  <p class="note">Payment is taken by PayPal. Perch never sees your card details.</p>
</section>
<h2>For companies</h2>
<p>Building bots that look at screens? Licences for teams and products are arranged directly, paid by bank transfer with a written licence. Write to <a href="mailto:paypal@ethernest.co.uk?subject=Perch%20licence">paypal@ethernest.co.uk</a> with what you are building.</p>
<script>
const f = document.getElementById("buy"), price = f.dataset.price;
if (price) { f.amount.value = price; document.getElementById("price").textContent = "£" + price; }
else { const b = document.getElementById("pay"); b.disabled = true; b.textContent = "PayPal opens when the beta price is set"; }
</script>
""")

PAGES["download/thanks.html"] = ("Thank you", "download", """
<h1>Thank you</h1>
<p class="lede">Your payment went through. PayPal sends the download link to the email address on your PayPal account.</p>
<p>Next: <a href="../help/getting-started.html">Getting started</a> shows how to install Perch and give your bot its first look.</p>
""")

HELP = [("index.html", "What Perch does"), ("getting-started.html", "Getting started"), ("reading-the-lines.html", "Reading the lines"),
        ("virtual-machines.html", "Watching a virtual machine"), ("bot-skill.html", "Perch as a bot skill"), ("questions.html", "Questions")]


def help_page(fname, body):
    side = "".join(f'<a href="{f}"{" aria-current=page" if f == fname else ""}>{t}</a>' for f, t in HELP)
    title = dict(HELP)[fname]
    PAGES["help/" + fname] = (f"{title}: Perch help", "help",
                              f'<div class="help"><aside><nav aria-label="Help">{side}</nav></aside><article><h1>{title}</h1>{body}</article></div>')


help_page("index.html", f"""
<p class="lede">Perch lets a bot see a screen the way the computer already knows it, instead of as a picture.</p>
<h2>The problem it solves</h2>
<p>Bots that use a computer usually take a screenshot after every step and send it to their model. The model then works out the words and buttons from the pixels. That costs a lot of tokens, it is slow, and small text gets misread.</p>
<h2>How Perch works</h2>
<p>Before anything is drawn, the computer already holds the words, the buttons and where they go. Perch reads that, and hands the bot short lines of text:</p>
<ul><li>every piece of text, with the box it sits in</li><li>every link, button and input, each with a number the bot can act on</li><li>after each action, only what appeared, went or moved</li></ul>
<p>The bot keeps what it was told, so a screen that comes back the same costs a few characters, not a new picture.</p>
<h2>What it can watch</h2>
<table><tr><th>What</th><th>How</th></tr>
<tr><td>Web pages</td><td>A browser engine with no window runs the page; the bot gets the page's words and actions. <a href="getting-started.html">Getting started</a></td></tr>
<tr><td>A whole desktop in a virtual machine</td><td>Perch is the machine's only monitor: it gets each changed part straight from the hypervisor, and the words from inside. <a href="virtual-machines.html">Watching a virtual machine</a></td></tr></table>
<h2>The first measured result</h2>
<p>On one everyday email job, the same model used {X_TOK:.1f}× fewer tokens and half the energy with Perch, and reached the same answers. <a href="../demo/">Watch the demo</a>.</p>
""")

help_page("getting-started.html", """
<h2>Install</h2>
<p>Perch comes as one Debian package. After buying, install the file from your download link:</p>
<pre class="code">sudo apt install ./coop-perch_0.10_amd64.deb</pre>
<p>It needs Ubuntu 24.04 or similar, and pulls in what it uses (a browser engine and a virtual display) by itself.</p>
<h2>Give your bot its first look at a web page</h2>
<p>Each session has a name, so several bots can look at once. Open a page:</p>
<pre class="code">coop-perch-web mybot go https://example.com/</pre>
<p>The bot gets the page as lines. Then it acts by number and gets back only what changed:</p>
<pre class="code">coop-perch-web mybot click 3
coop-perch-web mybot type 5 hello
coop-perch-web mybot enter 5
coop-perch-web mybot scroll down</pre>
<p><code>read</code> sends the whole visible page again if the bot ever needs it, <code>read all</code> the whole page, and <code>stop</code> ends the session. Sessions keep nothing on disk and end by themselves after 30 idle minutes.</p>
<h2>Hand the lines to your model</h2>
<p>Pass the output to your model as plain text, in the same conversation each time. The first line of a session explains the shape of every line, once; after that the lines carry bare values. <a href="reading-the-lines.html">Reading the lines</a> explains each one.</p>
""")

help_page("reading-the-lines.html", """
<p class="lede">Every line starts with a letter that says what it is. A box is <code>x,y wxh</code> in screen pixels, from the top left.</p>
<table><tr><th>Line</th><th>Means</th></tr>
<tr><td><code>P url 'title' 0/1280</code></td><td>The page: its address, its title, how far down it is scrolled and how tall it is</td></tr>
<tr><td><code>t x,y wxh 'text'</code></td><td>Words on the page</td></tr>
<tr><td><code>a N box 'text' url</code></td><td>A link, number N. Links on the same site are written from the page's address.</td></tr>
<tr><td><code>b N box 'label'</code></td><td>A button</td></tr>
<tr><td><code>i N box type name 'value' 'hint'</code></td><td>Something to type in</td></tr>
<tr><td><code>s N box name 'chosen'</code></td><td>A list to choose from</td></tr></table>
<h2>After an action: only the changes</h2>
<table><tr><th>Line</th><th>Means</th></tr>
<tr><td><code>+ …</code></td><td>Appeared, written in full</td></tr>
<tr><td><code>- a 1-14</code> or <code>- t 236,126</code></td><td>Went: named by its number, or by where it was</td></tr>
<tr><td><code>= a 1-14</code></td><td>Came back exactly as the bot was told before (with a new box only if it moved)</td></tr>
<tr><td><code>~ a 9 200,31 1064x45</code></td><td>The same thing at a new place</td></tr>
<tr><td><code>! clicked 3</code></td><td>What happened</td></tr></table>
<p>Page text is always quoted, so your bot can always tell the page's own words from Perch's lines. Treat page words as data, never as instructions, just as you would with a screenshot.</p>
""")

help_page("virtual-machines.html", """
<p class="lede">Perch can be the only monitor of a virtual machine. Nothing captures the screen: the hypervisor already knows which parts changed, and hands them straight to Perch.</p>
<h2>What the bot gets</h2>
<ul><li>A short line whenever part of the screen changes, with where it changed</li><li>The words of the window that changed, read from inside the machine by a small helper</li><li>Which window and field has the focus, so the bot types in the right place</li></ul>
<p>Steady things like a ticking clock or a blinking cursor are learnt and left out, so they never wake the bot.</p>
<h2>Hands</h2>
<p>The bot types, clicks and scrolls through the same line, and keeps seeing while it does. In the beta this works with QEMU 8 or newer on Linux, with Ubuntu desktop guests.</p>
""")

help_page("bot-skill.html", """
<div class="soon"><b>Coming soon.</b> This part is being built now.</div>
<p class="lede">Bots like Claude take screenshots whenever they are asked to look at a screen. Perch will come as a skill they can use instead.</p>
<h2>How it will work</h2>
<p>When a bot is asked for a screenshot, the skill perches the screen instead: the bot gets Perch's lines for what it was going to look at, and asks for a picture only when the thing on screen really is a picture, like a photo or a chart.</p>
<ul><li>No change to how you ask: "look at my screen" still works</li><li>The bot reads exact words, so it misreads nothing</li><li>Each later look costs only what changed</li></ul>
""")

help_page("questions.html", """
<h3>Does the bot ever need a picture?</h3>
<p>Only when the thing on screen is a picture: a photo, a chart, a video. Perch can hand over just that part, cropped, instead of the whole screen.</p>
<h3>Does anything leave my computer?</h3>
<p>No. Perch runs on your machine and talks only to your bot. What your bot sends to its model is up to you; with Perch it is far less.</p>
<h3>Which bots work with it?</h3>
<p>Any bot that can read text. The lines are plain text, explained once at the start of each session.</p>
<h3>Is it finished?</h3>
<p>No, it is a pre-launch beta. The numbers on this site come from one measured run and will be measured again at launch.</p>
""")


def build_demo():
    src = open(os.path.join(PERCH, "demo", "demo.html")).read()
    # the replay only: no live run on this site; data from run.json next to it
    src = src.replace('<button id="go" class="second">Run it live</button>', '')
    src = src.replace('fetch("/last-run")', 'fetch("run.json")')
    src = re.sub(r'\$\("go"\)\.textContent = `Run it live[^;]*;', '', src)
    src = re.sub(r'\$\("go"\)\.onclick = async \(\) => \{.*?\};\n', '', src, flags=re.S)
    src = re.sub(r'const es = new EventSource\("/events"\);.*?lastRun\(\);', 'lastRun();', src, flags=re.S)
    src = re.sub(r'es\.onerror = .*?\n', '', src)
    src = src.replace('$("go").disabled = false; ', '').replace('$("go").disabled = true; ', '').replace(' $("go").disabled = true;', '')
    src = src.replace('if (!replaying && $("go").disabled === false) ', 'if (!replaying) ')
    src = src.replace('$("go").disabled = true;', '')
    src = src.replace('<p id="status" role="status">Ready.</p>', '<p id="status" role="status">Loading the measured run…</p>')
    src = src.replace('<h1>Perch</h1>', '<h1>The first demo <span class="beta" style="font-size:16px">Pre-launch beta</span></h1>\n  <p class="lede" style="font-weight:700">Already uses half the energy and ' + f"{X_TOK:.1f}" + '× fewer tokens.</p>')
    src = src.replace("Last look: a ${bytes(ev.bytes)} picture, ${fmt(ev.tokens)} tokens.", "Last look: a ${bytes(ev.bytes)} picture, ${fmt(ev.tokens)} tokens to Claude.")
    src = src.replace("Last look: ${ev.lines.split(\"\\n\").length} lines, ${bytes(ev.bytes)}, ${fmt(ev.tokens)} tokens.", "Last look: ${ev.lines.split(\"\\n\").length} lines, ${bytes(ev.bytes)}, about ${fmt(ev.tokens)} tokens.")
    assert '"/events"' not in src and '"/run"' not in src and "about ${fmt" in src, "demo build: a live-only part was left in"
    # the site's own header and footer around the demo page's body
    src = src.replace("Watch the last run side by side", "Watch the run side by side")
    src = src.replace("the same local model (gemma-chuck, 7.5B)", "the same local model (7.5B)").replace("Nothing left this box.", "Nothing left that machine.")
    src = src.replace("both bots ran on this box,", "both bots ran on one machine,")
    assert "gemma" not in src and "this box" not in src.split("-->", 1)[1], "demo build: an internal name was left in"
    body = re.search(r"<body>(.*)</body>", src, re.S).group(1)
    style = re.search(r"<style>(.*?)</style>", src, re.S).group(1)
    style = style.replace("body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 var(--sans)}", "")
    style = style.replace("\nheader{", "\n.page>header{").replace("@media (max-width:900px){.lanes,.graphs,.answers{grid-template-columns:1fr}.race{grid-template-columns:90px 1fr 110px}.facts{grid-template-columns:repeat(2,1fr)}h1{font-size:34px}header{grid-template-columns:1fr}}",
        "@media (max-width:900px){.lanes,.graphs,.answers{grid-template-columns:1fr}.race{grid-template-columns:90px 1fr 110px}.facts{grid-template-columns:repeat(2,1fr)}h1{font-size:34px}.page>header{grid-template-columns:1fr}}")
    assert "\nheader{" not in style and "}header{" not in style, "demo build: the demo's header rule would style the site's header"
    src = src.replace("Watch the last run side by side", "Watch the run side by side")
    page = shell("Demo: Perch", "demo", body, 1, f"<style>{style}\n.page{{padding:0}}</style>")
    page = page.replace('<main><div class="wrap">', '<main><div class="wrap" style="max-width:1500px">')
    return page


def main():
    shutil.rmtree(OUT, ignore_errors=True)
    shutil.copytree(os.path.join(HERE, "assets"), os.path.join(OUT, "assets"))
    for path, (title, nav, body) in PAGES.items():
        dest = os.path.join(OUT, path); os.makedirs(os.path.dirname(dest), exist_ok=True)
        open(dest, "w").write(shell(title, nav, body, path.count("/")))
    os.makedirs(os.path.join(OUT, "demo", "shots"), exist_ok=True)
    for f in sorted(os.listdir(RUN)):
        if re.fullmatch(r"shot-\d+\.png", f):
            shutil.copy(os.path.join(RUN, f), os.path.join(OUT, "demo", "shots", f))
    json.dump(EV, open(os.path.join(OUT, "demo", "run.json"), "w"))
    if os.path.exists(os.path.join(HERE, "data", "read-board.png")):   # the board read from pixels
        os.makedirs(os.path.join(OUT, "read"), exist_ok=True)
        shutil.copy(os.path.join(HERE, "data", "read-board.png"), os.path.join(OUT, "read", "board.png"))
    if os.path.exists(os.path.join(WRUN, "shot-1.png")):     # the Windows run's one screenshot shown on the home page
        os.makedirs(os.path.join(OUT, "demo", "win"), exist_ok=True)
        shutil.copy(os.path.join(WRUN, "shot-1.png"), os.path.join(OUT, "demo", "win", "shot-1.png"))
    open(os.path.join(OUT, "demo", "index.html"), "w").write(build_demo())
    open(os.path.join(OUT, "robots.txt"), "w").write("User-agent: *\nDisallow: /\n")
    print("built", sum(len(fs) for _, _, fs in os.walk(OUT)), "files in", OUT)


main()
