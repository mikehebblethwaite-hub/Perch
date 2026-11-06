# Copyright (c) 2026 Mike J Hebblethwaite. See LICENSE.md.
"""perch_pdf: a PDF as a page perch-web can read like any other.

A web engine shows a PDF through its own plug-in viewer (or not at all, off-screen), and the bot gets nothing. So perch-web
hands the PDF here first. Each page becomes a block the width of the window, the PDF's own words placed where the PDF draws
them (from the file's text, never OCR), its links as links, its pictures as pictures, and the page as drawn (poppler's render)
laid behind the words as one picture per page. The bot then reads, scrolls, clicks, asks `url N` and `pixels N` exactly as on a
web page, and pixels show the page as the PDF really looks (lines, drawings, colour), not a re-typeset copy.

  build(pdf, out_dir, width, name) -> (index.html path, title, pages)   name: the title when the PDF has none

Needs poppler-utils (pdfinfo, pdftohtml, pdftoppm). A PDF from the web is untrusted: its words are only ever text here
(escaped), and only http(s) and mailto links are kept as links; script links and launch actions never reach the page.
"""
import html, os, re, subprocess, urllib.parse
import xml.etree.ElementTree as ET

TIMEOUT = 300                        # a safety bound on poppler for a hostile file, not a limit on size or pages
GAP = 24                             # the bar above each page that says which page it is


def _run(cmd):
    return subprocess.run(cmd, capture_output=True, timeout=TIMEOUT)


def info(pdf):
    out = _run(["pdfinfo", "-enc", "UTF-8", pdf]).stdout.decode("utf-8", "replace")
    d = {}
    for l in out.splitlines():
        k, _, v = l.partition(":")
        d[k.strip()] = v.strip()
    return d


def _href(h):
    """the links kept: the web and mail, and the PDF's own pages (#pN). Anything else (javascript:, launch, file) is dropped."""
    h = (h or "").strip()
    m = re.search(r"#(\d+)$", h)
    if m and not re.match(r"(?i)^(https?|mailto):", h):
        return f"#p{int(m.group(1))}"
    return h if re.match(r"(?i)^(https?://|mailto:)", h) else None


def _inner(el):
    """a <text> run's contents as safe HTML: words escaped, <b>/<i> kept, links filtered"""
    out = [html.escape(el.text or "")]
    for c in el:
        body = _inner(c)
        if c.tag in ("b", "i"):
            out.append(f"<{c.tag}>{body}</{c.tag}>")
        elif c.tag == "a":
            h = _href(c.get("href"))
            out.append(f'<a href="{html.escape(h, quote=True)}">{body}</a>' if h else body)
        else:
            out.append(body)
        out.append(html.escape(c.tail or ""))
    return "".join(out)


def build(pdf, out_dir, width=1280, name=None):
    os.makedirs(out_dir, exist_ok=True)
    meta = info(pdf)
    title = meta.get("Title") or name or os.path.basename(pdf)
    # the words, links and pictures with their places (pictures written beside, as PNG)
    r = _run(["pdftohtml", "-xml", "-q", "-nodrm", "-fmt", "png", pdf, os.path.join(out_dir, "x")])
    xml = os.path.join(out_dir, "x.xml")
    if not os.path.exists(xml):
        raise ValueError("not a PDF poppler can read: " + r.stderr.decode("utf-8", "replace").strip()[:200])
    root = ET.parse(xml).getroot()
    pages = root.findall("page")
    # each page as drawn, the window's width
    _run(["pdftoppm", "-scale-to-x", str(width), "-scale-to-y", "-1", "-jpeg", "-jpegopt", "quality=85", pdf,
          os.path.join(out_dir, "page")])
    renders = sorted((f for f in os.listdir(out_dir) if f.startswith("page-") and f.endswith(".jpg")),
                     key=lambda f: int(re.search(r"(\d+)\.jpg$", f).group(1)))
    n = len(pages)
    body = []
    for i, pg in enumerate(pages, 1):
        pw = float(pg.get("width") or 1); ph = float(pg.get("height") or 1); s = width / pw
        fonts = {f.get("id"): f for f in pg.iter("fontspec")}
        # fontspecs of earlier pages carry on (poppler lists each font once)
        for f in root.iter("fontspec"):
            fonts.setdefault(f.get("id"), f)
        body.append(f'<div class="bar" id="p{i}">page {i} of {n}</div>')
        body.append(f'<section style="height:{round(ph * s)}px">')
        if i - 1 < len(renders):
            body.append(f'<img class="pg" src="{renders[i - 1]}" alt="page {i} of {n} as drawn">')
        for im in pg.findall("image"):
            src = os.path.basename(im.get("src") or "")
            if not src or not os.path.exists(os.path.join(out_dir, src)):
                continue
            x, y, w, h = (float(im.get(k) or 0) * s for k in ("left", "top", "width", "height"))
            if w * h >= 0.9 * width * ph * s:
                continue                     # the page's own background (a printed web page's colour): the page picture has it
            body.append(f'<img class="im" src="{html.escape(src, quote=True)}" alt="picture on page {i}" '
                        f'style="left:{x:.0f}px;top:{y:.0f}px;width:{w:.0f}px;height:{h:.0f}px">')
        for t in pg.findall("text"):
            inner = _inner(t)
            if not inner.strip():
                continue
            f = fonts.get(t.get("font"))
            size = float(f.get("size") if f is not None else 12) * s
            bold = f is not None and "bold" in (f.get("family") or "").lower()
            x, y, w, h = (float(t.get(k) or 0) * s for k in ("left", "top", "width", "height"))
            body.append(f'<span style="left:{x:.0f}px;top:{y:.0f}px;width:{w:.0f}px;height:{h:.0f}px;'
                        f'font-size:{size:.1f}px{";font-weight:bold" if bold else ""}">{inner}</span>')
        body.append("</section>")
    doc = f"""<!doctype html><html><head><meta charset="utf-8"><title>{html.escape(title)}</title><style>
html,body{{margin:0;background:#888}} body{{width:{width}px;font-family:sans-serif}}
.bar{{height:{GAP}px;line-height:{GAP}px;font-size:13px;color:#fff;padding-left:6px;background:#555}}
section{{position:relative;width:{width}px;overflow:hidden;background:#fff}}
img.pg{{position:absolute;left:0;top:0;width:100%;height:100%}} img.im{{position:absolute;opacity:0.01}}
span{{position:absolute;white-space:pre;line-height:1;color:transparent;overflow:visible}}
a{{color:transparent}}
</style></head><body>
{chr(10).join(body)}
</body></html>"""
    idx = os.path.join(out_dir, "index.html")
    with open(idx, "w", encoding="utf-8") as fh:
        fh.write(doc)
    return idx, title, n
