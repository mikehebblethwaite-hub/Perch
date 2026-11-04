# Copyright (c) 2026 Mike J Hebblethwaite. See LICENSE.md.
# coop-perch-guest.ps1: Perch's helper inside a Windows guest. The meaning layer: what the screen SAYS, read where it already
# exists (the apps' UI Automation tree), never read back from pixels. The Windows twin of coop-perch-guest (Linux, AT-SPI): the same
# short lines, out through the virtio-serial port org.coop.perch.0 (not the network) to coop-perch-vm on the host, which checks,
# caps and quotes everything (the guest is not trusted).
#   T app x,y wxh +'text'   text there now (box = where it is on the screen)
#   F app role 'name' x,y wxh   focus moved here          W app 'title' x,y wxh   window became active (its words follow as T lines)
# Text: a changed element is noted; when it goes quiet (300 ms, or every 1.5 s while busy) the text that is there NOW is read from
# the element and sent once; the same text again is not re-sent. Long text: several lines of 200 characters, in order, never cut.
# Run it in the signed-in user's desktop session (UI Automation needs that desktop): coop-perch-winvm installs it as a logon task.
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
    static int myPid = Process.GetCurrentProcess().Id;
    static IntPtr myConsole = IntPtr.Zero;
    // the helper's own (hidden) console window is never what the screen shows: Windows may still give it focus at start
    static bool Mine(AutomationElement e) {
        try {
            if (e.Current.ProcessId == myPid) return true;
            var top = TopWindow(e);
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
    static string windowKey = "";
    static readonly Dictionary<int, string> procName = new Dictionary<int, string>();

    static void Emit(string line) {
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
            var w = TreeWalker.ControlViewWalker; int n = 0;   // no child holds words of its own (scroll bars and the like are fine)
            for (var c = w.GetFirstChild(e); c != null && n < 50; c = w.GetNextSibling(c), n++) {
                var cct = c.Current.ControlType;
                if (cct != ControlType.ScrollBar && cct != ControlType.Button && cct != ControlType.Thumb && !string.IsNullOrEmpty(Words(c))) return false;
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
    static void SendText(AutomationElement e, string k) {
        if (Surface(e)) { SendLines(e, k); return; }
        string text = Words(e);
        if (string.IsNullOrEmpty(text)) return;
        try { if (e.Current.IsOffscreen) return; } catch (Exception) { return; }
        string old;
        if (k != null && sent.TryGetValue(k, out old) && old == text) return;
        if (k != null) { if (sent.Count > 4000) sent.Clear(); sent[k] = text; }
        string app = App(e), box = Box(e);
        for (int i = 0; i < text.Length; i += CHUNK)
            Emit("T " + app + " " + box + " +" + Q(text.Substring(i, Math.Min(CHUNK, text.Length - i))));
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
        int s = 0;
        foreach (var e in order) { if (s >= SNAP_MAX) break; if (Surface(e) || !string.IsNullOrEmpty(Words(e))) { SendText(e, Key(e)); s++; } }
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
        foreach (var kv in due) SendText(kv.Value.el, kv.Key);
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
            if (tk != windowKey) {
                if (window != null) { try { Automation.RemoveAllEventHandlers(); } catch (Exception) { } Automation.AddAutomationFocusChangedEventHandler(OnFocus); }
                window = top; windowKey = tk;
                string title = ""; try { title = top.Current.Name ?? ""; } catch (Exception) { }
                Emit("W " + App(top) + " " + Q(title.Length > 120 ? title.Substring(0, 120) : title) + " " + Box(top));
                Snapshot(top);
                try { Watch(top); } catch (Exception) { }
            }
            string name = ""; try { name = e.Current.Name ?? ""; } catch (Exception) { }
            Emit("F " + App(e) + " " + Role(e) + " " + Q(name.Length > 120 ? name.Substring(0, 120) : name) + " " + Box(e));
        } catch (Exception) { }
    }
    public static void Run(string port) {
        try { SetProcessDPIAware(); } catch (Exception) { }
        Stream s;
        if (!string.IsNullOrEmpty(port)) s = new FileStream(port, FileMode.Append, FileAccess.Write, FileShare.ReadWrite);
        else {
            var h = CreateFile(@"\\.\Global\org.coop.perch.0", 0x40000000, 0, IntPtr.Zero, 3, 0, IntPtr.Zero);
            if (h.IsInvalid) throw new IOException("no port org.coop.perch.0 (virtio-serial driver, -device virtserialport): error " + Marshal.GetLastWin32Error());
            s = new FileStream(h, FileAccess.Write, 1);
        }
        outw = new StreamWriter(s, new UTF8Encoding(false));
        Emit("O guest-helper-win 0.02");
        Automation.AddAutomationFocusChangedEventHandler(OnFocus);
        myConsole = GetConsoleWindow();
        try {   // start with what is in front now (the focus may be on our own hidden console)
            AutomationElement f = AutomationElement.FocusedElement;
            if (f == null || Mine(f)) { var h = GetForegroundWindow(); f = h != IntPtr.Zero ? AutomationElement.FromHandle(h) : null; }
            if (f != null && !Mine(f)) OnFocus(f, null);
        } catch (Exception) { }
        while (true) { Thread.Sleep(100); try { Flush(false); } catch (Exception) { } }
    }
}
'@
[PerchGuest]::Run($env:PERCH_PORT)
