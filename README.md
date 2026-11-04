# Perch: The Bot's Eye on the Display

Perch is an open-source tool that lets bots and automated systems see your screen in real-time, the way you do. Instead of taking screenshots and burning through tokens on OCR, Perch acts as a virtual display adaptor, capturing what the system draws as it draws it—delivering text as text, images as images, all with minimal overhead.

## Why Perch?

**The Problem:** Bots working with screens have to screenshot everything, then burn tokens on vision models to parse pixels back into meaning. It's slow, expensive, and prone to errors.

**The Solution:** Be the screen itself. Perch intercepts the display stream before it reaches the monitor, so you get direct access to:
- Text and text regions (no OCR needed)
- Dirty rectangles (only what changed)
- Input coordination (keyboard and mouse, with proper queuing)
- Low latency, minimal token cost

## Features

- **Linux:** Virtual framebuffer via Xvfb with DAMAGE extension for change detection
- **Windows:** Indirect display driver support
- **macOS:** Virtual display API integration
- **Cross-platform:** Core library works on Linux, Windows, and macOS
- **Real-time:** Dirty rectangle tracking for efficient updates
- **Text-first:** Accessibility layers (AT-SPI, DOM, terminal buffers) before pixel fallback

## Architecture

```
Display Stream
     ↓
Perch (virtual display adaptor)
     ├→ Text Layer (DOM, AT-SPI, terminal, accessibility)
     ├→ Region Layer (dirty rectangles, layout)
     ├→ Image Layer (pixel fallback)
     └→ >SCR Bus (text, regions, watch, input commands)
```

## Interface: >SCR Commands

- `>SCR text` — all readable text on screen, with positions
- `>SCR region x1,y1,x2,y2` — one area, as text where possible
- `>SCR changed` — only the dirty rectangles since last look
- `>SCR watch region x1,y1,x2,y2` — subscribe to changes in a region
- `>SCR read terminal` — read a region by name (terminal, error panel, etc.)
- `>SCR pixels region` — last resort, actual image crop
- Input: `>KEY`, `>CLICK x,y`, `>TYPE` — share one input queue with humans

## Building

```bash
make
```

## Files

- `coop-perch-eye.c` — Core display capture and text extraction
- `coop-perch-feed.c` — Feed daemon and message passing
- `coop-perch-web` — Web interface for viewing live screen and replay
- `coop-perch-vm*` — Virtual machine integration
- `site/` — Demo site and documentation
- `demo/` — Example usage and tests

## Quick Start

See `site/demo/` for examples and the included demo scripts.

## Support Perch

If Perch saves you money, time, or tokens—consider supporting the project:

- **[Ko-fi](https://ko-fi.com/mikehebblethwaite)** — One-time or recurring donations
- **[GitHub Sponsors](https://github.com/sponsors/mikehebblethwaite)** — Recurring sponsorship

Every contribution helps keep Perch free and open for everyone.

## License

Free under the GNU AGPL-3.0 (if you share it or run it as a service, your source must be open too), or under a paid commercial licence for closed products. See LICENSE.md; contact mike.hebblethwaite@gmail.com.

---

Perch © 2026 Mike J Hebblethwaite
Contact: mike.hebblethwaite@gmail.com
