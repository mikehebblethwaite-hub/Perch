CFLAGS ?= -O2 -Wall -Wextra
all: coop-perch-eye coop-perch-feed coop-perch-tap coop-perch-box coop-perch-chip coop-perch-hen coop-perch-wait coop-perch-crop coop-perch-scene coop-perch-shotbot coop-perch-cam coop-perch-vm
coop-perch-eye: coop-perch-eye.c coop-perch-box.h coop-perch-core.h
	$(CC) $(CFLAGS) -o $@ $< -lX11 -lXext -lXdamage -lXfixes -lm
coop-perch-feed: coop-perch-feed.c
	$(CC) $(CFLAGS) -o $@ $< -lX11
coop-perch-tap: coop-perch-tap.c coop-perch-box.h
	$(CC) $(CFLAGS) -o $@ $<
coop-perch-box: coop-perch-box.c coop-perch-box.h
	$(CC) $(CFLAGS) -o $@ $<
coop-perch-chip: coop-perch-chip.c
	$(CC) $(CFLAGS) -o $@ $< -lX11
coop-perch-hen: coop-perch-hen.c
	$(CC) $(CFLAGS) -o $@ $< -lX11
coop-perch-wait: coop-perch-wait.c coop-perch-box.h
	$(CC) $(CFLAGS) -o $@ $<
coop-perch-crop: coop-perch-crop.c
	$(CC) $(CFLAGS) -o $@ $< -lX11 -lz
coop-perch-scene: coop-perch-scene.c
	$(CC) $(CFLAGS) -o $@ $< -lX11
coop-perch-shotbot: coop-perch-shotbot.c
	$(CC) $(CFLAGS) -o $@ $< -lX11 -lz
coop-perch-cam: coop-perch-cam.c coop-perch-box.h
	$(CC) $(CFLAGS) -o $@ $<
clean:
	rm -f coop-perch-eye coop-perch-feed coop-perch-tap coop-perch-box coop-perch-chip coop-perch-hen coop-perch-wait coop-perch-crop coop-perch-scene coop-perch-shotbot coop-perch-cam coop-perch-vm; rm -rf dist
coop-perch-vm: coop-perch-vm.c coop-perch-core.h coop-perch-box.h
	$(CC) $(CFLAGS) -o $@ $< $(shell pkg-config --cflags --libs gio-2.0 gio-unix-2.0) -lm

# ---- the product: install, and a .deb of the VM eye (binaries only, stripped; no source goes out) ----
VERSION ?= $(shell cat VERSION)
PREFIX ?= /usr/local
PRODUCT_BIN = coop-perch-vm coop-perch-box coop-perch-wait coop-perch-crop
PRODUCT_SCRIPTS = coop-perch-vmrun coop-perch-act coop-perch-web coop-perch-vmlook coop-perch-report coop-perch-look
install: $(PRODUCT_BIN)
	install -d $(DESTDIR)$(PREFIX)/bin $(DESTDIR)$(PREFIX)/share/coop-perch/guest $(DESTDIR)$(PREFIX)/lib/coop-perch
	install -m 644 coop_perch_read.py $(DESTDIR)$(PREFIX)/lib/coop-perch/
	install -s -m 755 $(PRODUCT_BIN) $(DESTDIR)$(PREFIX)/bin/
	install -m 755 $(PRODUCT_SCRIPTS) $(DESTDIR)$(PREFIX)/bin/
	install -m 755 coop-perch-guest $(DESTDIR)$(PREFIX)/share/coop-perch/guest/
	install -m 644 coop-perch-guest.ps1 vm-win/perch-guest-task.ps1 $(DESTDIR)$(PREFIX)/share/coop-perch/guest/
	install -m 644 LICENSE LICENSE.md README.md $(DESTDIR)$(PREFIX)/share/coop-perch/
deb: $(PRODUCT_BIN)
	rm -rf dist/pkg && mkdir -p dist/pkg/DEBIAN
	$(MAKE) install DESTDIR=$(CURDIR)/dist/pkg PREFIX=/usr
	printf 'Package: coop-perch\nVersion: $(VERSION)\nArchitecture: amd64\nMaintainer: Mike J Hebblethwaite\nDepends: qemu-system-x86 (>= 1:8.0), dbus-daemon | dbus, libglib2.0-0t64 | libglib2.0-0, python3, python3-pyatspi, gir1.2-atspi-2.0, gir1.2-webkit2-4.1, xvfb\nSection: misc\nPriority: optional\nRecommends: tesseract-ocr\nDescription: Perch: a virtual machine screen for bots, without screenshots\n The guest screen comes straight from the hypervisor as changed boxes and\n raw vectors; text from the guest helper; keys and mouse back the same way.\n' > dist/pkg/DEBIAN/control
	printf 'Copyright (c) 2026 Mike J Hebblethwaite. GNU AGPL-3.0 or a commercial licence: see /usr/share/coop-perch/LICENSE.md\n' > dist/pkg/DEBIAN/copyright
	dpkg-deb --root-owner-group --build dist/pkg dist/coop-perch_$(VERSION)_amd64.deb
