#!/usr/bin/python3 -I
# Copyright (c) 2026 Mike J Hebblethwaite. See LICENSE.md.
"""perch_pdf + perch-web on PDFs (make test). Builds its own PDFs (no files from anywhere), then checks:
  honest: every page's words, in order, at their places; bold kept; a web link clickable (url N gives it); a link to page 3
          goes to page 3; the P line carries the PDF's address and pages; pixels show the page as drawn.
  tricks: a javascript: link and a launch action never become links; words shaped like HTML/script stay words (escaped);
          a file that is not a PDF answers with what went wrong, never hangs or shows an old page.
"""
import os, re, subprocess, sys, tempfile, zlib

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import perch_pdf  # noqa: E402

def make_pdf(path, pages, links):
    """a small real PDF: pages = [[(x, y, size, bold, text), ...], ...] (PDF points, y from the bottom);
    links = [(page, (x0, y0, x1, y1), action_dict_text)]"""
    objs = []
    def add(b): objs.append(b); return len(objs)
    f1 = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    f2 = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")
    pages_id = len(objs) + 1 + 2 * len(pages) + len(links) + 1   # placeholder, fixed below
    kids, page_ids = [], []
    content_ids = []
    for pg in pages:
        ops = []
        for x, y, size, bold, text in pg:
            t = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            ops.append(f"BT /{'F2' if bold else 'F1'} {size} Tf {x} {y} Td ({t}) Tj ET")
        data = zlib.compress("\n".join(ops).encode("latin-1"))
        content_ids.append(add(b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(data) + data + b"\nendstream"))
    annot_ids = {}
    for pi, rect, action in links:
        annot_ids.setdefault(pi, []).append(None)
    # pages (annotations need page ids for GoTo, so reserve ids first)
    first_page = len(objs) + 1
    n_annots = len(links)
    page_ids = [first_page + i for i in range(len(pages))]
    pages_id = first_page + len(pages) + n_annots
    annots_by_page = {}
    aid = first_page + len(pages)
    annot_objs = []
    for pi, rect, action in links:
        action = action.replace("PAGE3", f"{page_ids[2]} 0 R" if len(page_ids) > 2 else "null")
        annot_objs.append(("<< /Type /Annot /Subtype /Link /Rect [%d %d %d %d] /Border [0 0 0] /A %s >>" % (*rect, action)).encode())
        annots_by_page.setdefault(pi, []).append(aid); aid += 1
    for i, pg in enumerate(pages):
        an = " ".join(f"{a} 0 R" for a in annots_by_page.get(i, []))
        add((f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 595 842] /Contents {content_ids[i]} 0 R "
             f"/Resources << /Font << /F1 {f1} 0 R /F2 {f2} 0 R >> >>" + (f" /Annots [{an}]" if an else "") + " >>").encode())
    for a in annot_objs: add(a)
    assert len(objs) + 1 == pages_id
    add(f"<< /Type /Pages /Kids [{' '.join(f'{p} 0 R' for p in page_ids)}] /Count {len(pages)} >>".encode())
    info = add(b"<< /Title (Perch PDF test) >>")
    cat = add(f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode())
    out = bytearray(b"%PDF-1.4\n"); offs = []
    for i, b in enumerate(objs, 1):
        offs.append(len(out)); out += b"%d 0 obj\n" % i + b + b"\nendobj\n"
    x = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offs)
    out += b"trailer\n<< /Size %d /Root %d 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, cat, info, x)
    open(path, "wb").write(out)

fails = []
def check(ok, what):
    print(("PASS " if ok else "FAIL ") + what)
    if not ok: fails.append(what)

d = tempfile.mkdtemp(prefix="perch-pdf-test-")
pdf = os.path.join(d, "test.pdf")
make_pdf(pdf, [
    [(72, 760, 24, True, "Page one heading"), (72, 720, 12, False, "Plain words on page one."),
     (72, 690, 12, False, "Our site: example.org"), (72, 660, 12, False, "Go to page three"),
     (72, 630, 12, False, "<script>alert('x')</script> & 'quotes'"), (72, 600, 12, False, "Run me (javascript)"),
     (72, 570, 12, False, "Launch me")],
    [(72, 760, 12, False, "Second page words.")],
    [(72, 760, 18, True, "Page three"), (72, 720, 12, False, "The end.")],
], [
    (0, (70, 685, 250, 702), "<< /S /URI /URI (https://example.org/perch) >>"),
    (0, (70, 655, 250, 672), "<< /S /GoTo /D [PAGE3 /XYZ 0 842 0] >>"),
    (0, (70, 595, 250, 612), "<< /S /URI /URI (javascript:alert\\(1\\)) >>"),
    (0, (70, 565, 250, 582), "<< /S /Launch /F (/bin/sh) >>"),
])

# ---- perch_pdf on its own ----
out = os.path.join(d, "out")
idx, title, n = perch_pdf.build(pdf, out, 1280)
h = open(idx, encoding="utf-8").read()
check(n == 3, f"3 pages found ({n})")
check(title == "Perch PDF test", f"title from the PDF ({title!r})")
for w in ("Page one heading", "Plain words on page one.", "Second page words.", "Page three", "The end."):
    check(w in h, f"words kept: {w}")
check(h.index("Page one heading") < h.index("Second page words.") < h.index("The end."), "pages in order")
check(re.search(r"(font-weight:bold\">|<b>)Page one heading", h) is not None, "bold kept")
check('href="https://example.org/perch"' in h, "web link kept")
check('href="#p3"' in h, "link to page 3 kept as #p3")
check("javascript:" not in h, "javascript: link dropped")
check("/bin/sh" not in h and "Launch me" in h, "launch action dropped, its words kept")
check("<script>" not in h and "&lt;script&gt;" in h, "script-shaped words escaped")
check(all(os.path.exists(os.path.join(out, f"page-{i}.jpg")) for i in (1, 2, 3)), "each page drawn")

# ---- perch-web end to end ----
web = os.path.join(HERE, "perch-web"); name = "pdftest%d" % os.getpid()
def pw(*a):
    return subprocess.run([web, name, *a], capture_output=True, text=True, timeout=120).stdout
try:
    r = pw("--mode=words", "go", pdf) if False else pw("go", pdf)
    r = pw("--mode", "words", "read", "all")
    head = r.splitlines()[0] if r else ""
    check(head.startswith("P file://" + pdf) and head.endswith("pdf:3"), f"P line: the PDF's address and pages ({head[:90]})")
    check("Page one heading*'" in r and "'Second page words.'" in r and "'The end.'" in r, "perch-web reads the words")
    check(re.search(r"\*Page one heading\*", r) is not None, "perch-web marks bold")
    m = re.search(r"^a (\d+) \S+ \S+ 'Our site: example.org' //example.org/perch$", r, re.M)
    check(m is not None, "web link is an a line (another site: //host/path)")
    check(m is not None and pw("url", m.group(1)).strip() == f"url {m.group(1)} https://example.org/perch", "url N gives the whole address")
    check("javascript" not in r.replace("'Run me (javascript)'", ""), "no javascript link reaches the bot")
    m3 = re.search(r"^a (\d+) \S+ \S+ 'Go to page three'", r, re.M)
    if m3:
        c = pw("--mode", "words", "click", m3.group(1))
        sy = re.search(r"^P \S+ '[^']*' (\d+)/", pw("where") and pw("--mode", "words", "read"), re.M)
        check(sy is not None and int(sy.group(1)) > 1500, f"click: went to page 3 (scroll {sy.group(1) if sy else '?'})")
    else:
        check(False, "link to page 3 is an a line")
    bad = os.path.join(d, "bad.pdf"); open(bad, "wb").write(b"not a pdf at all\n")
    r = pw("go", bad)
    check("PDF could not be read" in r, f"not a PDF: says so ({r.strip()[:80]})")
finally:
    pw("stop")

print(("ALL PASS" if not fails else f"{len(fails)} FAIL") + f" ({d})")
sys.exit(1 if fails else 0)
