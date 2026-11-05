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
    def item(key, href, text):
        cur = " aria-current=page" if key == nav else ""
        if key != "demo":
            return f'<a href="{up}{href}"{cur}>{text}</a>'
        subs = "".join(f'<a href="{up}demo/{k}/">{html.escape(t)}</a>' for k, t, *_ in DEMOS())
        return (f'<details class="drop"><summary{" class=cur" if cur else ""}>{text}<svg viewBox="0 0 12 12" aria-hidden="true"><path d="M2.5 4.5 6 8l3.5-3.5" '
                f'fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg></summary>'
                f'<div class="drop-menu"><a href="{up}demo/">All demos</a>{subs}</div></details>')
    links = "".join(item(*n) for n in NAV)
    return f"""<!doctype html>
<!-- Copyright (c) 2026 Mike J Hebblethwaite. All rights reserved. Perch is open-source: https://github.com/mikehebblethwaite-hub/Perch -->
<html lang="en-GB"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<link rel="stylesheet" href="{up}assets/site.css">{extra_head}</head><body>
<header class="top"><div class="wrap"><a class="mark" href="{up}">{MARK}Perch</a><nav aria-label="Site">{links}</nav></div></header>
<main><div class="wrap">{body}</div></main>
<script>document.addEventListener("click",e=>{{document.querySelectorAll("details.drop[open]").forEach(d=>{{if(!d.contains(e.target))d.open=false}})}});
document.addEventListener("keydown",e=>{{if(e.key==="Escape")document.querySelectorAll("details.drop[open]").forEach(d=>{{d.open=false;d.querySelector("summary").focus()}})}});</script>
<footer><div class="wrap">Perch © 2026 Mike J Hebblethwaite. <a href="https://github.com/mikehebblethwaite-hub/Perch">Open source on GitHub</a> · <a href="https://ko-fi.com/mikehebblethwaite">Support on Ko-fi</a></div></footer>
</body></html>
"""


def run_data():
    ev = json.load(open(os.path.join(RUN, "run.json")))
    for e in ev:
        if e.get("img"):
            e["img"] = re.sub(r"^/tape/(shot-\d+\.png).*$", r"../shots/\1", e["img"])
    res = next(e for e in ev if e["kind"] == "done")["result"]
    return ev, res


EV, RES = run_data()
S, P = RES["shot"], RES["perch"]
X_TOK = S["claude"] / P["claude"]
X_TIME = S["wall"] / P["wall"]
E_SHARE = P["j"] / S["j"]
PERCH_SEE = [e for e in EV if e.get("lane") == "perch" and e["kind"] == "see"]
# the same job on a Windows desktop (demo/perch-demo-win): its own measured run
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
    """the learning reader: one measured fair test (data/learn-test.json, from demo/perch-read-test on the Windows VM)"""
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
<div class="cta"><a class="btn solid" href="demo/">See the demos</a><a class="btn" href="help/">How it works</a></div>

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
<p class="note">Measured on one run, on one machine, with a 7.5B model on its processor. Tokens are counted as a chat model like Claude bills them, with each screenshot at Claude's 1,365. <a href="demo/screens/">See the whole run</a>.</p>

{win_section()}
{read_section()}
{learn_section()}
<h2>Video, the bot's way</h2>
<p style="max-width:68ch">Perch also plays videos for bots: no frames, just timed lines of what was said, shown and heard. Claude watched a
whole 89-minute Stanford lecture this way and wrote a summary of it from those lines alone.</p>
<div class="cta"><a class="btn" href="demo/video/">Read the lecture summary</a></div>
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
<pre class="code">sudo apt install ./perch_0.10_amd64.deb</pre>
<p>It needs Ubuntu 24.04 or similar, and pulls in what it uses (a browser engine and a virtual display) by itself.</p>
<h2>Give your bot its first look at a web page</h2>
<p>Each session has a name, so several bots can look at once. Open a page:</p>
<pre class="code">perch-web mybot go https://example.com/</pre>
<p>The bot gets the page as lines. Then it acts by number and gets back only what changed:</p>
<pre class="code">perch-web mybot click 3
perch-web mybot type 5 hello
perch-web mybot enter 5
perch-web mybot scroll down</pre>
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



# ---------------- the demos: one page per thing Perch can do; a new ability adds a line here ----------------
def DEMOS():
    v = {}
    try: v = json.load(open(os.path.join(HERE, "data", "video-demo.json")))
    except (OSError, ValueError): pass
    return [
        ("screens", "Screens without screenshots",
         "One email job done twice by the same model: once seeing screenshots, once seeing the screen through Perch. Watch both side by side.",
         f"{X_TOK:.1f}× fewer tokens", "the same answers, in a third of the time"),
        ("video", "Video, the bot's way",
         "Claude watched an 89-minute Stanford lecture as timed lines of speech, slide words and sounds, with no frames, and wrote a summary from them.",
         f"{v.get('lines', 0):,} lines", f"for an {round(v.get('dur', 5343) / 60)}-minute lecture, about {v.get('chars', 0) // 4:,} tokens"),
    ]


def SUBNAV(here):
    links = "".join(f'<a href="../{k}/"{" aria-current=page" if k == here else ""}>{html.escape(t)}</a>' for k, t, *_ in DEMOS())
    return f'<nav class="subnav" aria-label="Demos"><a href="../">All demos</a>{links}</nav>'


SUBNAV_CSS = """<style>.subnav{display:flex;flex-wrap:wrap;gap:4px 6px;margin:-12px 0 26px;font-size:15px}
.subnav a{padding:5px 12px;border-radius:20px;border:1px solid var(--rule);color:var(--ink2);text-decoration:none;background:var(--panel)}
.subnav a:hover{border-color:var(--ink2)} .subnav a[aria-current]{background:var(--ink);color:var(--bg);border-color:var(--ink)}</style>"""


def demo_hub():
    cards = "".join(f"""<a class="demo-card" href="{k}/"><h2>{html.escape(t)}</h2><p>{html.escape(d)}</p>
<div class="demo-num"><b>{html.escape(n)}</b>{html.escape(m)}</div><span class="demo-go">Open the demo</span></a>""" for k, t, d, n, m in DEMOS())
    body = f"""<h1>Demos</h1>
<p class="lede">Each one is a real, measured run of something Perch can do. New ones arrive as Perch learns them.</p>
<div class="demos">{cards}</div>"""
    css = """<style>.demos{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:22px;margin-top:30px}
.demo-card{display:flex;flex-direction:column;background:var(--panel);border:1px solid var(--rule);border-radius:14px;padding:20px 22px;color:var(--ink);text-decoration:none}
.demo-card:hover{border-color:var(--perch)} .demo-card h2{margin:0 0 6px;font-size:24px} .demo-card p{margin:0 0 16px;color:var(--ink2);flex:1}
.demo-num{border-top:3px solid var(--rule);padding-top:8px;color:var(--ink2);font-size:15px} .demo-num b{display:block;font-size:26px;color:var(--ink)}
.demo-go{margin-top:16px;font-weight:700;color:var(--perch)}</style>"""
    return shell("Demos: Perch", "demo", body, 1, css)


def build_demo():
    src = open(os.path.join(PERCH, "demo", "demo.html")).read()
    # the replay only: no live run on this site; data from run.json next to it
    src = src.replace('<button id="go" class="second">Run it live</button>', '')
    src = src.replace('fetch("/last-run")', 'fetch("../run.json")')
    src = re.sub(r'\$\("go"\)\.textContent = `Run it live[^;]*;', '', src)
    src = re.sub(r'\$\("go"\)\.onclick = async \(\) => \{.*?\};\n', '', src, flags=re.S)
    src = re.sub(r'const es = new EventSource\("/events"\);.*?lastRun\(\);', 'lastRun();', src, flags=re.S)
    # the site opens on the finished run (every number and look in place); the button still replays it at its real pace
    i_ = src.rindex("lastRun();"); src = src[:i_] + src[i_:].replace("lastRun();", 'lastRun().then(ev => { if (!ev) return; const sv = Element.prototype.scrollIntoView; Element.prototype.scrollIntoView = () => {}; reset(); show({ kind: "start", img_tokens: (ev.find(e => e.kind === "start") || {}).img_tokens || 1365 }); '
                      'for (const e of ev) if (e.lane) show(e); const d = ev.find(e => e.kind === "done"); if (d) show(d); Element.prototype.scrollIntoView = sv; $("status").textContent = "Below is how the run ended. Press play to watch both bots do it from the start: the real measured run, sped up 10×."; });', 1)
    src = re.sub(r'es\.onerror = .*?\n', '', src)
    src = src.replace('$("go").disabled = false; ', '').replace('$("go").disabled = true; ', '').replace(' $("go").disabled = true;', '')
    src = src.replace('if (!replaying && $("go").disabled === false) ', 'if (!replaying) ')
    src = src.replace('$("go").disabled = true;', '')
    src = src.replace("`Watch the last run side by side (${SPEED}× speed, ", "`▶ Play it from the start (${SPEED}× speed, ")
    src = src.replace("`Last run ${when}. A replay shows that run's real measured timings, both bots started together, sped up ${SPEED}×.`",
                      "`Below is how the run ended. Press play to watch both bots do it from the start: the real measured run, sped up ${SPEED}×.`")
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
    ask = re.search(r'<div class="bubble">.*?</div>', body, re.S).group(0)
    top = f"""<div class="dtop">
  <h1>The first demo <span class="beta" style="font-size:16px">Pre-launch beta</span></h1>
  <p class="lede">One everyday job, done twice by the same local model with the same hands. One bot sees the screen as screenshots,
  the other through Perch: the words and buttons, then only what changed.</p>
  <h2 class="dres">Perch used {X_TOK:.1f}× fewer tokens for the same job</h2>
  <div class="dsum">
    <div><b>{P["claude"]:,}</b>tokens with Perch, against {S["claude"]:,} with screenshots</div>
    <div><b>{S["j"] / P["j"]:.1f}× less energy</b>{P["j"] / 1000:.1f} kJ against {S["j"] / 1000:.1f} kJ</div>
    <div><b>{X_TIME:.1f}× quicker</b>{P["wall"] / 60:.0f} minutes against {S["wall"] / 60:.0f}</div>
    <div><b>4 of 4 found</b>both bots found the emails that need a reply</div>
  </div>
  <h2 class="dwatch">Watch it happen</h2>
  {ask}
  <div class="dplay"><button id="replay" hidden>▶ Play it from the start</button><p id="status" role="status">Loading the measured run…</p></div>
</div>"""
    body = re.sub(r'<header>.*?</header>\s*<p id="status" role="status">.*?</p>', lambda m_: top, body, count=1, flags=re.S)
    assert body.count('id="replay"') == 1 and body.count('id="status"') == 1, "demo build: the top was not replaced"
    dstyle = """
.dtop .lede{max-width:62ch;font-size:19px}
.dres{font-size:30px;margin:30px 0 10px}
.dsum{display:grid;grid-template-columns:repeat(4,1fr);gap:20px;margin:0 0 8px}
.dsum div{border-top:3px solid var(--rule);padding-top:8px;color:var(--ink2);font-size:15px}
.dsum b{display:block;font-size:24px;color:var(--ink)}
.dwatch{font-size:30px;margin:44px 0 12px}
.dq{margin:0 0 8px;color:var(--ink2)}
.dtop .bubble{max-width:760px}
.dplay{display:flex;align-items:center;gap:18px;flex-wrap:wrap;margin:16px 0 26px}
.dplay button{font-size:19px;padding:14px 26px;background:var(--perch);color:#fff}
.dplay #status{margin:0;flex:1;min-width:240px;font-size:15px;color:var(--ink2)}
@media (max-width:700px){.dsum{grid-template-columns:repeat(2,1fr)}.dplay button{width:100%}.dres,.dwatch{font-size:25px}}"""
    style = re.search(r"<style>(.*?)</style>", src, re.S).group(1)
    style = style.replace("body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 var(--sans)}", "")
    style = style.replace("\nheader{", "\n.page>header{").replace("@media (max-width:900px){.lanes,.graphs,.answers{grid-template-columns:1fr}.race{grid-template-columns:90px 1fr 110px}.facts{grid-template-columns:repeat(2,1fr)}h1{font-size:34px}header{grid-template-columns:1fr}}",
        "@media (max-width:900px){.lanes,.graphs,.answers{grid-template-columns:1fr}.race{grid-template-columns:90px 1fr 110px}.facts{grid-template-columns:repeat(2,1fr)}h1{font-size:34px}.page>header{grid-template-columns:1fr}}")
    assert "\nheader{" not in style and "}header{" not in style, "demo build: the demo's header rule would style the site's header"
    src = src.replace("Watch the last run side by side", "Watch the run side by side")
    body = SUBNAV("screens") + body
    page = shell("Screens without screenshots: Perch demo", "demo", body, 2, f"<style>{style}\n.page{{padding:0}}{dstyle}</style>" + SUBNAV_CSS)
    page = page.replace('<main><div class="wrap">', '<main><div class="wrap" style="max-width:1500px">')
    return page



# ---------------- the bot video player: a lecture summary Claude made from Perch's lines (data/video-demo.json) ----------------
WATCH = "Stanford CS336 Language Modeling from Scratch | Spring 2026 | Lecture 3: Architectures"
# the lecture's own parts, as Claude read them from the lines (slide words + speech); times are where each part starts
CHAPTERS = [
    (0, "What this lecture is for",
     "<p>A quick recap of the transformer the class builds, then two questions: what do almost all big language models have in common, "
     "and where do they differ? The slide gives the theme: the best way to learn is hands-on experience; the second best is to learn "
     "from other people's.</p>"),
    (100, "The original transformer against a modern one",
     "<p>The 2017 design used post-norm LayerNorm, sine and cosine position embeddings and ReLU. A modern model puts the norm in front "
     "of each block, uses rotary position embeddings (RoPE) and SwiGLU, and drops the bias terms. The rest of the lecture asks why.</p>"),
    (173, "Too many models to keep up with",
     "<p>The slides list more than 19 new dense model releases in 2024 and 2025 alone: Gemma 3, Llama 3, Qwen2, Nemotron-4, Granite 4.0, "
     "MiniMax, Kimi K2, ERNIE 4.5, Step-3 and more. I think the lecturer also gave “a shout out to Percy's own 8B model, trained with "
     "Marin”. With so many papers, the lecture learns from what they agree on.</p>"),
    (444, "Pre-norm won",
     "<p>“The one thing everyone agrees on”: put the norm where it doesn't touch the main residual path. Almost every modern model is "
     "pre-norm (BERT was post-norm; OPT-350M is the odd one out). Once the reason was skipping learning-rate warm-up; now it's stability "
     "and larger learning rates. Newer still is a second norm outside the residual stream (Grok, Gemma 2; OLMo 2 uses only that one).</p>"),
    (828, "RMSNorm, and FLOPs are not runtime",
     "<p>Most models swapped LayerNorm for RMSNorm: no mean, no bias, fewer numbers to move. A table makes the point of the day: "
     "normalisation is 0.17% of the FLOPs but 25.5% of the runtime, because moving data costs more than arithmetic. Bias terms went "
     "for the same reasons, memory and stable training.</p>"),
    (1203, "A zoo of activations, and the GLUs won",
     "<p>ReLU, GeLU, Swish and the gated kind. A GLU multiplies the first part of the feed-forward layer by a second linear term. "
     "SwiGLU is in LLaMA, PaLM, Mistral, OLMo and most models since 2023; GeGLU is in T5 v1.1 and Gemma 2, 3 and 4. Gated models shrink "
     "the hidden size by 2/3 to keep the parameter count. The gains are fairly consistent, though GPT-3 shows you don't need one.</p>"),
    (1621, "Serial or parallel blocks",
     "<p>Attention and the MLP can run side by side in one block: about 15% faster training at large scale (PaLM; also Cohere Command "
     "and Falcon 2 11B). Most models still run them one after the other.</p>"),
    (1858, "Position embeddings: RoPE",
     "<p>Sine, absolute and relative embeddings all fall short of one goal: attention should depend only on how far apart two words "
     "are. Inner products don't change when both vectors turn by the same angle, so RoPE rotates pairs of coordinates in two "
     "dimensions by an amount set by the position. It is applied inside every attention layer, not once at the bottom.</p>"),
    (2327, "Hyperparameters most models agree on",
     "<p>The feed-forward layer is about 4 times the model width (8/3 for GLUs). T5 11B went 64 times and its follow-up came back "
     "to 2.5. Heads × head size usually equals the model width. Width against depth: a ratio of roughly 100 to 200, since very deep "
     "models are harder to spread across machines and slower to answer. Vocabularies: 30–50k entries for one language, 100–250k "
     "for many.</p>"),
    (3390, "Regularisation that isn't about overfitting",
     "<p>With trillions of tokens and one pass over them, models hardly overfit, and newer ones drop dropout. Yet weight decay "
     "stays. The lecture's explanation: it doesn't control overfitting at all; it works with the learning-rate schedule, so runs "
     "with stronger decay start slower and end better.</p>"),
    (3836, "Keeping training stable: watch the softmaxes",
     "<p>Exponentials and division make softmaxes the usual cause of loss spikes. Three fixes: the output “z-loss” (PaLM, Baichuan 2, "
     "OLMo 2 and 3), QK-norm on queries and keys before attention (from vision models; now Gemma 2, Qwen3, OLMo 3, Gemma 4), and "
     "soft-capping the logits (Gemma 2: 50 in attention, 30 at the output).</p>"),
    (4424, "Cheaper attention: MQA, GQA and sliding windows",
     "<p>Generating text one token at a time is limited by memory traffic for the KV cache, not arithmetic. Multi-query attention "
     "shares one key and value head; grouped-query attention shares a few, with little or no loss. Sliding-window attention looks only "
     "nearby; the current trick is to interleave full and windowed layers (Cohere Command A; Gemma 4, OLMo 3, Qwen 3.5).</p>"),
    (5310, "What to take away",
     "<p>Many parts of the big models are now the same. The real differences are position embeddings, activations and tokenisation. "
     "The lecture ends by sending students off to try these ideas on their assignments.</p>"),
]

VIDEO_STYLE = """<style>
:root{--teal:#0f8a7e} @media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--teal:#3cc2b2}} :root[data-theme="dark"]{--teal:#3cc2b2}
.made{display:inline-flex;gap:10px;align-items:center;background:var(--panel);border:1px solid var(--rule);border-radius:30px;padding:6px 16px 6px 8px;font-size:15px;color:var(--ink2);margin:0 0 22px}
.made svg{width:26px;height:26px;color:var(--ink);flex:none}
.v-h1{max-width:22ch}
.tape{position:relative;margin:34px 0 8px;padding:28px 0 22px}
.tape svg{display:block;width:100%;height:clamp(76px,11vw,112px);overflow:visible}
.tape .pins{position:absolute;inset:0 0 auto 0;height:24px}
.pin{position:absolute;top:0;transform:translateX(-50%);width:24px;height:24px;border-radius:50%;background:var(--ink);color:var(--bg);
  font:700 12px/24px var(--sans);text-align:center;text-decoration:none}
.pin::after{content:"";position:absolute;left:50%;top:22px;width:2px;height:10px;margin-left:-1px;background:var(--ink)}
.pin.first{transform:none} .pin.first::after{left:1px}
.pin:hover,.pin:focus-visible{background:var(--perch)} .pin:hover::after,.pin:focus-visible::after{background:var(--perch)}
.tape .ticks{position:absolute;left:0;right:0;bottom:0;height:18px;font:12px var(--mono);color:var(--muted)}
.tape .ticks span{position:absolute;transform:translateX(-50%)} .tape .ticks span:first-child{transform:none}
.tape .now{position:absolute;top:24px;bottom:20px;width:3px;margin-left:-1px;background:var(--perch);display:none;pointer-events:none}
.legend{display:flex;flex-wrap:wrap;gap:6px 18px;font-size:14px;color:var(--ink2)}
.legend i{display:inline-block;width:12px;height:12px;border-radius:3px;margin-right:6px;vertical-align:-1px}
.chapters{list-style:none;padding:0;margin:8px 0 0;max-width:820px}
.chapters li{display:grid;grid-template-columns:82px 1fr;gap:18px;padding:18px 0;border-top:1px solid var(--rule)}
.chapters li.on{background:linear-gradient(90deg,color-mix(in srgb,var(--perch) 12%,transparent),transparent 70%)}
.chapters .at{font:15px var(--mono);color:var(--perch);text-decoration:none;padding-top:3px;font-variant-numeric:tabular-nums}
.chapters h3{margin:0 0 4px;font-size:20px} .chapters p{margin:0;max-width:68ch}
.small-bot{margin:28px 0 0;max-width:820px;border-left:4px solid var(--rule);padding:4px 0 4px 18px;color:var(--ink2)}
.small-bot p{margin:0;font-size:16px} .small-bot footer{font-size:14px;color:var(--muted);margin-top:8px}
.moments details{border-top:1px solid var(--rule);max-width:900px}
.moments summary{cursor:pointer;padding:14px 0;display:flex;gap:12px;align-items:baseline;list-style:none}
.moments summary::-webkit-details-marker{display:none}
.moments .n{flex:none;width:24px;height:24px;border-radius:50%;background:var(--ink);color:var(--bg);font-size:13px;font-weight:700;display:grid;place-items:center}
.moments .at{font:14px var(--mono);color:var(--muted)}
.moments details[open] .n{background:var(--perch)}
.moments p{margin:0 0 10px 36px;max-width:68ch;color:var(--ink2)}
.moments pre{margin:0 0 18px 36px;background:var(--screen);border:1px solid var(--rule);border-radius:8px;padding:10px 12px;font:12.5px/1.55 var(--mono);white-space:pre-wrap;word-break:break-word}
.watch{display:grid;grid-template-columns:1fr 1fr;gap:28px;max-width:1000px}
.watch .card p{margin:6px 0}
@media (max-width:600px){.tape .ticks span:nth-child(even){display:none}}
@media (max-width:820px){.watch{grid-template-columns:1fr}.chapters li{grid-template-columns:64px 1fr;gap:12px}.facts{grid-template-columns:repeat(2,1fr)}
  .moments p,.moments pre{margin-left:0}}
@media (prefers-reduced-motion:no-preference){.chapters li{transition:background .3s}}
</style>"""

VIDEO_SCRIPT = """<script>
const tape = document.querySelector('.tape'), now = tape.querySelector('.now'), DUR = %s;
function mark(t){ now.style.left = (t / DUR * 100) + '%%'; now.style.display = 'block'; }
document.querySelectorAll('.chapters .at').forEach(a => a.addEventListener('click', () => {
  document.querySelectorAll('.chapters li.on').forEach(l => l.classList.remove('on'));
  a.closest('li').classList.add('on'); mark(+a.dataset.t); }));
function openPin(){ const d = location.hash && document.getElementById(location.hash.slice(1)); if (d && d.tagName === 'DETAILS') d.open = true; }
window.addEventListener('hashchange', openPin); openPin();
</script>"""


def vts(t):
    m, s = divmod(int(t), 60); h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def video_page():
    try:
        d = json.load(open(os.path.join(HERE, "data", "video-demo.json")))
    except (OSError, ValueError):
        return None
    dur, nb = d["dur"], len(d["lanes"]["say"])
    W, LH, GAP = 1000.0, 22, 6
    lanes = [("say", "Speech heard", "var(--perch)"), ("text", "Words on screen", "var(--teal)"),
             ("sound", "Other sounds", "var(--shot)"), ("cut", "Scene changes", "var(--ink)")]
    svg = []
    for i, (k, name, col) in enumerate(lanes):
        y = i * (LH + GAP); top = max(1, max(d["lanes"][k]))
        svg.append(f'<rect x="0" y="{y}" width="{W:.0f}" height="{LH}" fill="var(--screen)"/>')
        for j, c in enumerate(d["lanes"][k]):
            if c:
                svg.append(f'<rect x="{j * W / nb:.1f}" y="{y}" width="{W / nb + .4:.1f}" height="{LH}" fill="{col}" opacity="{.3 + .7 * c / top:.2f}"/>')
    hgt = len(lanes) * (LH + GAP) - GAP
    for t, _, _ in CHAPTERS[1:]:
        svg.append(f'<line x1="{t / dur * W:.1f}" x2="{t / dur * W:.1f}" y1="0" y2="{hgt}" stroke="var(--bg)" stroke-width="2"/>')
    pins = "".join(f'<a class="pin{" first" if m["t"] / dur < .02 else ""}" href="#m{i}" style="left:{m["t"] / dur * 100:.2f}%" aria-label="{html.escape(m["title"])}, at {vts(m["t"])}">{i + 1}</a>'
                   for i, m in enumerate(d["moments"]))
    legend = "".join(f'<span><i style="background:{c}"></i>{n}</span>' for _, n, c in lanes)
    ticks = "".join(f'<span style="left:{t / dur * 100:.2f}%">{vts(t)}</span>' for t in range(0, int(dur) - 300, 900))
    chapters = "".join(f'<li><a class="at" href="#tape" data-t="{t}">{vts(t)}</a><div><h3>{html.escape(title)}</h3>{body}</div></li>'
                       for t, title, body in CHAPTERS)
    moments = "".join(f'<details id="m{i}"><summary><span class="n">{i + 1}</span><b>{html.escape(m["title"])}</b> <span class="at">{m["at"]}</span></summary>'
                      f'<p>{html.escape(m["note"])}</p><pre aria-label="The exact lines the bot was given here">{html.escape(chr(10).join(m["lines"]))}</pre></details>'
                      for i, m in enumerate(d["moments"]))
    c = d["count"]; kb = d["chars"] / 1000; tok = d["chars"] // 4
    sure = d.get("sure") or []
    took = (f'<div><b>{d["took"] / 60:.0f} minutes</b>to turn the whole lecture into lines, on one home machine with no cloud</div>'
            if d.get("took") else "")
    o = d["odd"]
    odd_txt = "now and then " + ((f"a pig ({o['Pig']} times)" + (" and a frog" if o.get("Frog") else "")) if o.get("Pig") else "an animal")
    summ = (f'<blockquote class="small-bot"><p>{html.escape(d["summary"])}</p><footer>Perch\'s own short summary, made on the same machine by a '
            f'small local model from the same lines with no help. It is a best guess and says so.</footer></blockquote>') if d.get("summary") else ""
    sure_note = (f" Every line of speech says how sure the speech reader was, from 1 to 11; the middle one here was {sorted(sure)[len(sure) // 2]}."
                 if sure else "")
    q_watch = WATCH.replace(" ", "+").replace("|", "%7C").replace(":", "%3A")
    body = f"""
<p class="made">{MARK}This video summary page was made using the Perch video player for bots by Claude.</p>
<h1 class="v-h1">A bot watched an 89-minute lecture. Here is what it took away.</h1>
<p class="lede">The lecture is Stanford CS336, Lecture 3: Architectures. Claude didn't look at a single frame. Perch played the video
and gave it timed lines instead: what was said, what was on the slides, what else could be heard and where the scenes changed.
Everything below was written from those lines.</p>

<div class="tape" id="tape" aria-label="The whole lecture as the bot got it, in 30-second slices">
  <div class="pins">{pins}</div>
  <svg viewBox="0 0 {W:.0f} {hgt}" preserveAspectRatio="none" role="img" aria-label="Four rows across {d["dur_ts"]}: speech, words on screen, other sounds and scene changes. Darker means more lines in that half minute.">{"".join(svg)}</svg>
  <div class="now"></div>
  <div class="ticks">{ticks}</div>
</div>
<div class="legend">{legend}<span>Gaps mark the lecture's parts. The numbered pins open the exact lines.</span></div>

<h2>The lecture, part by part</h2>
<ol class="chapters">{chapters}</ol>
{summ}

<h2>What the bot was given</h2>
<div class="facts">
  <div><b>{d["lines"]:,} lines</b>{c["say"]:,} of speech, {c["text"]:,} of slide words, {c["cut"]} scene changes</div>
  <div><b>{kb:.0f} KB of text</b>about {tok:,} tokens for the whole lecture; the video file is {(d.get("video_bytes") or 0) / 1e6:.0f} MB</div>
  {took}
  <div><b>No frames</b>not one picture was sent to Claude</div>
</div>
<p class="note">Speech is read by a local speech model in 30-second pieces, slide words by reading the pixels, other sounds by a sound
namer. All of them are best guesses, and the lines say so.{sure_note}</p>

<h2>What it heard, and what it misread</h2>
<p style="max-width:68ch">Besides the speech, the sound namer named the room's other sounds: footsteps, chewing, stomach rumbles, and
{odd_txt}. Those are normal in a lecture hall, and each comes as “it sounded like”, with how sure it was. The real misreadings are in
the words: small table text on a 640 × 360 video came out garbled, and the speech reader wrote SwiGLU as “Swigloo”. Each is a line
the bot can weigh, not a fact it has to believe. Open a moment to see the exact lines.</p>
<div class="moments">{moments}</div>

<h2>Watch it, then try it yourself</h2>
<div class="watch">
  <section class="card"><h3>Watch the lecture</h3>
    <p>We don't host the video. It's Stanford's, posted by Stanford Online on YouTube and shared on X. Look for its title:</p>
    <p><b>{html.escape(WATCH)}</b></p>
    <p><a href="https://www.youtube.com/results?search_query={q_watch}">Find it on YouTube</a> · <a href="https://cs336.stanford.edu/">The course's own site</a></p></section>
  <section class="card"><h3>Run Perch on it</h3>
    <p>Save the video, then:</p>
    <pre class="code">perch-video --summary lecture.mp4 &gt; lecture.lines</pre>
    <p>Or let a bot drive it as it plays, and go back over any part where the sound is poor:</p>
    <pre class="code">perch-video cs336 play lecture.mp4
perch-video cs336 read
perch-video cs336 set quality small
perch-video cs336 back 30</pre>
    <p class="note">Perch keeps what it learned on your machine, never the video. <a href="../help/">How Perch works</a></p></section>
</div>
"""
    return body, VIDEO_STYLE + (VIDEO_SCRIPT % dur)


def build_video():
    got = video_page()
    if not got: return
    body, extra = got
    page = shell("A bot watched a lecture: Perch's video player", "demo", SUBNAV("video") + body.replace('href="../help/"', 'href="../../help/"'), 2, extra + SUBNAV_CSS)
    os.makedirs(os.path.join(OUT, "demo", "video"), exist_ok=True)
    open(os.path.join(OUT, "demo", "video", "index.html"), "w").write(page)
    os.makedirs(os.path.join(OUT, "video"), exist_ok=True)               # the first address it had: sent on
    open(os.path.join(OUT, "video", "index.html"), "w").write('<!doctype html><meta charset="utf-8"><title>Moved</title>'
        '<meta http-equiv="refresh" content="0;url=../demo/video/"><link rel="canonical" href="../demo/video/">'
        '<p>This page moved to <a href="../demo/video/">the video demo</a>.</p>')


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
    os.makedirs(os.path.join(OUT, "demo", "screens"), exist_ok=True)
    open(os.path.join(OUT, "demo", "screens", "index.html"), "w").write(build_demo())
    open(os.path.join(OUT, "demo", "index.html"), "w").write(demo_hub())
    build_video()
    open(os.path.join(OUT, "robots.txt"), "w").write("User-agent: *\nDisallow: /\n")
    print("built", sum(len(fs) for _, _, fs in os.walk(OUT)), "files in", OUT)


main()
