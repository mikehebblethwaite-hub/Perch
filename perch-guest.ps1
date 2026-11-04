# Copyright (c) 2026 Mike J Hebblethwaite. See LICENSE.md.
# perch-guest.ps1: Perch's helper inside a Windows guest. The meaning layer: what the screen SAYS, read where it already
# exists (the apps' UI Automation tree), never read back from pixels. The Windows twin of perch-guest (Linux, AT-SPI): the same
# short lines, out through the virtio-serial port uk.co.ethernest.perch.0 (not the network) to perch-vm on the host, which checks,
# caps and quotes everything (the guest is not trusted).
#   T app x,y wxh +'text'   text there now (box = where it is on the screen)
#   F app role 'name' x,y wxh   focus moved here          W app 'title' x,y wxh   window became active (its words follow as T lines)
# Text: a changed element is noted; when it goes quiet (300 ms, or every 1.5 s while busy) the text that is there NOW is read from
# the element and sent once; the same text again is not re-sent. Long text: several lines of 200 characters, in order, never cut.
# Run it in the signed-in user's desktop session (UI Automation needs that desktop): perch-winvm installs it as a logon task.
# Needs nothing installed: Windows PowerShell 5.1 + .NET Framework (built in). PERCH_PORT=FILE writes to a file instead (tests).
$ErrorActionPreference = 'Stop'
Add-Type -ReferencedAssemblies UIAutomationClient, UIAutomationTypes, WindowsBase -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Windows.Automation;
using System.Windows.Automation.Text;
using Microsoft.Win32.SafeHandles;

public static class PerchGuest {
    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    static extern SafeFileHandle CreateFile(string name, uint access, uint share, IntPtr sa, uint disp, uint flags, IntPtr tmpl);
    [DllImport("user32.dll")] static extern bool SetProcessDPIAware();
    [DllImport("kernel32.dll")] static extern IntPtr GetConsoleWindow();
    [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
    static int myPid = Process.GetCurrentProcess().Id;
    static IntPtr myConsole = IntPtr.Zero;
    // the helper's own (hidden) console window is never what the screen shows: Windows may still give it focus at start
    static bool Mine(AutomationElement e) {
        try {
            if (e.Current.ProcessId == myPid) return true;
            var top = TopWindow(e);
            int hw = top.Current.NativeWindowHandle;
            if (hw != 0 && !IsWindowVisible(new IntPtr(hw))) return true;     // hidden windows (consoles of hidden tasks) are not the screen
            return myConsole != IntPtr.Zero && top.Current.NativeWindowHandle == myConsole.ToInt32();
        } catch (Exception) { return false; }
    }

    const int MAXTXT = 400, CHUNK = 200, SNAP_MAX = 300;
    static readonly TimeSpan QUIET = TimeSpan.FromMilliseconds(300), MAXWAIT = TimeSpan.FromMilliseconds(1500);
    static StreamWriter outw;
    static readonly object lk = new object();
    class Pend { public AutomationElement el; public DateTime first, last; }
    static readonly Dictionary<string, Pend> pending = new Dictionary<string, Pend>();
    static readonly Dictionary<string, string> sent = new Dictionary<string, string>();
    static AutomationElement window;
    static string lastF = "";
    static string windowKey = "";
    static readonly Dictionary<int, string> procName = new Dictionary<int, string>();

    static readonly bool Debug = Environment.GetEnvironmentVariable("PERCH_DEBUG") != null || File.Exists(@"C:\perch\debug");
    static void Emit(string line) {
        if (line.Contains(" ? +") && Debug) Console.Error.WriteLine("? line from: " + Environment.StackTrace.Replace("\r\n", " | ").Substring(0, 400));
        lock (lk) { try { outw.Write(line + "\n"); outw.Flush(); } catch (Exception) { } }
    }
    static string Q(string t) {
        t = t ?? ""; if (t.Length > MAXTXT) t = t.Substring(0, MAXTXT);
        return "'" + t.Replace("\\", "\\\\").Replace("'", "\\'").Replace("\n", "\\n").Replace("\r", "").Replace("\t", "\\t") + "'";
    }
    static string Word(string t, int n) {
        var sb = new StringBuilder();
        foreach (char c in (string.IsNullOrEmpty(t) ? "?" : t)) { sb.Append(char.IsLetterOrDigit(c) || c == '-' || c == '_' || c == '.' ? c : '_'); if (sb.Length >= n) break; }
        return sb.Length > 0 ? sb.ToString() : "?";
    }
    static string Box(AutomationElement e) {
        try {
            var r = e.Current.BoundingRectangle;
            if (r.IsEmpty || double.IsInfinity(r.Width)) return "?";
            return string.Format("{0},{1} {2}x{3}", (int)r.X, (int)r.Y, (int)r.Width, (int)r.Height);
        } catch (Exception) { return "?"; }
    }
    static string App(AutomationElement e) {
        try {
            int pid = e.Current.ProcessId; string n;
            if (!procName.TryGetValue(pid, out n)) { try { n = Word(Process.GetProcessById(pid).ProcessName, 24); } catch (Exception) { n = "?"; } procName[pid] = n; }
            return n;
        } catch (Exception) { return "?"; }
    }
    static string Role(AutomationElement e) {
        try { return Word(e.Current.ControlType.ProgrammaticName.Replace("ControlType.", "").ToLowerInvariant(), 20); } catch (Exception) { return "?"; }
    }
    static string Key(AutomationElement e) {
        try { return string.Join(".", e.GetRuntimeId()); } catch (Exception) { return null; }
    }
    // the words an element holds itself: an edit's value, else its own text pattern (not whole documents), else its name
    static string Words(AutomationElement e) {
        try {
            var ct = e.Current.ControlType;
            if (ct == ControlType.Document || ct == ControlType.Window || ct == ControlType.Pane || ct == ControlType.Group) return "";
            if (ct == ControlType.Hyperlink) return e.Current.Name ?? "";    // a link's value is its web address: its words are its name
            object p;
            if (e.TryGetCurrentPattern(ValuePattern.Pattern, out p)) { var v = ((ValuePattern)p).Current.Value; if (!string.IsNullOrEmpty(v)) return v.Length > 20000 ? v.Substring(0, 20000) : v; }
            if (ct == ControlType.Edit && e.TryGetCurrentPattern(TextPattern.Pattern, out p)) return ((TextPattern)p).DocumentRange.GetText(20000);
            return e.Current.Name ?? "";
        } catch (Exception) { return ""; }
    }
    // a text surface (a console, a plain text area): its words live in one text pattern, not in children. Send the lines that
    // are on screen now, each with its own box; a line is re-sent only when its words change.
    static bool Surface(AutomationElement e) {
        try {
            var ct = e.Current.ControlType;
            if (ct != ControlType.Document && ct != ControlType.Edit) return false;
            object p; if (!e.TryGetCurrentPattern(TextPattern.Pattern, out p)) return false;
            // no children but scroll bars and their parts (a console, a plain text area); a web page has a tree under it
            var w = TreeWalker.ControlViewWalker; int n = 0;
            for (var c = w.GetFirstChild(e); c != null && n < 50; c = w.GetNextSibling(c), n++) {
                var cct = c.Current.ControlType;
                if (cct != ControlType.ScrollBar && cct != ControlType.Button && cct != ControlType.Thumb) return false;
            }
            return true;
        } catch (Exception) { return false; }
    }
    static void SendLines(AutomationElement e, string k) {
        try {
            object p; if (!e.TryGetCurrentPattern(TextPattern.Pattern, out p)) return;
            var tp = (TextPattern)p; string app = App(e); int n = 0;
            // the visible text in one call, and one box per line from the same range: lines and boxes pair up in order
            // (when the counts differ, as when empty lines have no box, boxes go to the lines that have words)
            foreach (var vis in tp.GetVisibleRanges()) {
                string all = vis.GetText(100000) ?? "";
                var rects = vis.GetBoundingRectangles();
                var lines = all.Split('\n');
                int nonEmpty = 0; foreach (var l in lines) if (l.Trim().Length > 0) nonEmpty++;
                bool byLine = rects.Length == lines.Length;
                int ri = 0;
                for (int li = 0; li < lines.Length && n < 300; li++) {
                    string text = lines[li].TrimEnd(' ', '\r');
                    System.Windows.Rect rc = System.Windows.Rect.Empty;
                    if (byLine) rc = rects[li];
                    else if (text.Trim().Length > 0 && rects.Length == nonEmpty) rc = rects[ri++];
                    if (text.Trim().Length == 0) continue;
                    n++;
                    string box = rc.IsEmpty ? "?" : string.Format("{0},{1} {2}x{3}", (int)rc.X, (int)rc.Y, (int)rc.Width, (int)rc.Height);
                    string lk = (k ?? "?") + "@" + (rc.IsEmpty ? li.ToString() : box), old;
                    if (sent.TryGetValue(lk, out old) && old == text) continue;
                    if (sent.Count > 4000) sent.Clear();
                    sent[lk] = text;
                    for (int i = 0; i < text.Length; i += CHUNK)
                        Emit("T " + app + " " + box + " +" + Q(text.Substring(i, Math.Min(CHUNK, text.Length - i))));
                }
            }
        } catch (Exception x) { Console.Error.WriteLine("lines: " + x.GetType().Name + ": " + x.Message); }
    }
    static readonly Dictionary<string, DateTime> placeSeen = new Dictionary<string, DateTime>();
    static int PlaceY(AutomationElement e) {
        try { var r = e.Current.BoundingRectangle; return r.IsEmpty ? -1 : (int)(r.Y + r.Height / 2) / 6; } catch (Exception) { return -1; }
    }
    static void SendOne(AutomationElement e, string k, string text, string box) {
        string old;
        if (k != null && sent.TryGetValue(k, out old) && old == text) return;
        if (k != null) { if (sent.Count > 4000) sent.Clear(); sent[k] = text; }
        // a link and the text inside it hold the same words in the same place: the first one told is enough
        string place = text + "@" + box; DateTime when;
        if (placeSeen.TryGetValue(place, out when) && DateTime.UtcNow - when < TimeSpan.FromSeconds(2)) return;
        if (placeSeen.Count > 4000) placeSeen.Clear();
        placeSeen[place] = DateTime.UtcNow;
        string app = App(e);
        for (int i = 0; i < text.Length; i += CHUNK)
            Emit("T " + app + " " + box + " +" + Q(text.Substring(i, Math.Min(CHUNK, text.Length - i))));
    }
    class Cand { public AutomationElement e; public string k, t, box; public bool borrowed; }
    // a batch of elements (a window's snapshot, or the ones that changed): their words, with two kinds of noise taken out.
    // Edge reports some runs of text with no box: such a piece borrows the box of the nearest parent that has one, and is
    // dropped when it is only part of a longer line in the batch, or repeats a line that has its own box.
    static void SendBatch(List<AutomationElement> els) {
        var cand = new List<Cand>(); var boxed = new HashSet<string>(); var w = TreeWalker.ControlViewWalker;
        foreach (var e in els) {
            if (cand.Count >= SNAP_MAX) break;
            try { if (e.Current.IsOffscreen) continue; } catch (Exception) { continue; }
            string k = Key(e);
            if (Surface(e)) { SendLines(e, k); continue; }
            string t = Words(e); if (string.IsNullOrEmpty(t)) continue;
            string box = Box(e); bool borrowed = false;
            if (box == "?") {
                try { var p = w.GetParent(e); for (int i = 0; i < 4 && p != null && box == "?"; i++, p = w.GetParent(p)) box = Box(p); } catch (Exception) { }
                if (box == "?") continue;          // nowhere on the screen
                borrowed = true;
            } else boxed.Add(t);
            cand.Add(new Cand { e = e, k = k, t = t, box = box, borrowed = borrowed });
        }
        foreach (var c in cand) {
            if (c.borrowed) {
                if (boxed.Contains(c.t)) continue;
                bool part = false;
                foreach (var d in cand) if (d != c && d.t.Length > c.t.Length && d.t.Contains(c.t)) { part = true; break; }
                if (part) continue;
            }
            SendOne(c.e, c.k, c.t, c.box);
        }
    }
    // a window's words when it becomes active: every element on screen that holds words, in reading order of the tree
    static void Snapshot(AutomationElement root) {
        var w = TreeWalker.ControlViewWalker; int n = 0;
        var stack = new Stack<KeyValuePair<AutomationElement, int>>();
        stack.Push(new KeyValuePair<AutomationElement, int>(root, 0));
        var order = new List<AutomationElement>();
        while (stack.Count > 0 && n < SNAP_MAX * 4) {
            var kv = stack.Pop(); n++;
            order.Add(kv.Key);
            if (kv.Value > 40) continue;
            var kids = new List<AutomationElement>();
            try { for (var c = w.GetFirstChild(kv.Key); c != null; c = w.GetNextSibling(c)) kids.Add(c); } catch (Exception) { }
            for (int i = kids.Count - 1; i >= 0; i--) stack.Push(new KeyValuePair<AutomationElement, int>(kids[i], kv.Value + 1));
        }
        SendBatch(order);
    }
    static AutomationElement TopWindow(AutomationElement e) {
        var w = TreeWalker.ControlViewWalker; var root = AutomationElement.RootElement;
        try {
            for (var p = w.GetParent(e); p != null && !Automation.Compare(p, root); p = w.GetParent(p)) e = p;
        } catch (Exception) { }
        return e;
    }
    static void Mark(object src) {
        var e = src as AutomationElement; if (e == null) return;
        string k = Key(e); if (k == null) return;
        var now = DateTime.UtcNow;
        lock (pending) { Pend p; if (!pending.TryGetValue(k, out p)) pending[k] = p = new Pend { el = e, first = now }; p.el = e; p.last = now; }
    }
    static void Flush(bool all) {
        var now = DateTime.UtcNow; var due = new List<KeyValuePair<string, Pend>>();
        lock (pending) {
            foreach (var kv in pending) if (all || now - kv.Value.last >= QUIET || now - kv.Value.first >= MAXWAIT) due.Add(kv);
            foreach (var kv in due) pending.Remove(kv.Key);
        }
        var els = new List<AutomationElement>(); foreach (var kv in due) els.Add(kv.Value.el);
        if (els.Count > 0) SendBatch(els);
    }
    static void Watch(AutomationElement win) {
        Automation.AddAutomationPropertyChangedEventHandler(win, TreeScope.Subtree, (s, e) => Mark(s), AutomationElement.NameProperty, ValuePattern.ValueProperty);
        Automation.AddAutomationEventHandler(TextPattern.TextChangedEvent, win, TreeScope.Subtree, (s, e) => Mark(s));
        Automation.AddStructureChangedEventHandler(win, TreeScope.Subtree, (s, e) => {
            var el = s as AutomationElement; if (el == null) return;
            try { var w = TreeWalker.ControlViewWalker; for (var c = w.GetFirstChild(el); c != null; c = w.GetNextSibling(c)) Mark(c); } catch (Exception) { }
            Mark(el);
        });
    }
    static void OnFocus(object src, AutomationFocusChangedEventArgs ev) {
        var e = src as AutomationElement; if (e == null || Mine(e)) return;
        try {
            Flush(true);
            var top = TopWindow(e); string tk = Key(top) ?? "";
            if (Box(top) == "?") return;          // the window has already gone
            if (tk != windowKey) {
                if (window != null) { try { Automation.RemoveAllEventHandlers(); } catch (Exception) { } Automation.AddAutomationFocusChangedEventHandler(OnFocus); }
                window = top; windowKey = tk;
                string title = ""; try { title = top.Current.Name ?? ""; } catch (Exception) { }
                Emit("W " + App(top) + " " + Q(title.Length > 120 ? title.Substring(0, 120) : title) + " " + Box(top));
                Snapshot(top);
                try { Watch(top); } catch (Exception) { }
            }
            if (Box(e) == "?") return;            // focus on something that has already gone
            string name = ""; try { name = e.Current.Name ?? ""; } catch (Exception) { }
            string fl = "F " + App(e) + " " + Role(e) + " " + Q(name.Length > 120 ? name.Substring(0, 120) : name) + " " + Box(e);
            if (fl != lastF) { lastF = fl; Emit(fl); }
        } catch (Exception) { }
    }
    public static void Run(string port) {
        try { SetProcessDPIAware(); } catch (Exception) { }
        Stream s;
        if (!string.IsNullOrEmpty(port)) s = new FileStream(port, FileMode.Append, FileAccess.Write, FileShare.ReadWrite);
        else {
            var h = CreateFile(@"\\.\Global\uk.co.ethernest.perch.0", 0x40000000, 0, IntPtr.Zero, 3, 0, IntPtr.Zero);
            if (h.IsInvalid) throw new IOException("no port uk.co.ethernest.perch.0 (virtio-serial driver, -device virtserialport): error " + Marshal.GetLastWin32Error());
            s = new FileStream(h, FileAccess.Write, 1);
        }
        outw = new StreamWriter(s, new UTF8Encoding(false));
        Emit("O guest-helper-win 0.07");
        Automation.AddAutomationFocusChangedEventHandler(OnFocus);
        myConsole = GetConsoleWindow();
        try {   // start with what is in front now (the focus may be on our own hidden console)
            AutomationElement f = AutomationElement.FocusedElement;
            if (f == null || Mine(f)) { var h = GetForegroundWindow(); f = h != IntPtr.Zero ? AutomationElement.FromHandle(h) : null; }
            if (f != null && !Mine(f)) OnFocus(f, null);
        } catch (Exception) { }
        // focus events do not always come when a new window appears (Edge, for one): the window in front is checked as well
        IntPtr front = GetForegroundWindow(); int tick = 0;
        while (true) {
            Thread.Sleep(100);
            try { Flush(false); } catch (Exception) { }
            if (++tick % 3 != 0) continue;
            try {
                var h = GetForegroundWindow();
                if (h != front && h != IntPtr.Zero) {
                    front = h;
                    var w = AutomationElement.FromHandle(h);
                    if (w != null && !Mine(w)) {
                        AutomationElement f = null;
                        try { f = AutomationElement.FocusedElement; } catch (Exception) { }
                        OnFocus(f != null && !Mine(f) && Automation.Compare(TopWindow(f), w) ? f : w, null);
                    }
                }
            } catch (Exception) { }
        }
    }
}
'@
[PerchGuest]::Run($env:PERCH_PORT)
