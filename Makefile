CFLAGS ?= -O2 -Wall -Wextra
all: perch-eye perch-feed perch-tap perch-box perch-chip perch-hen perch-wait perch-crop perch-scene perch-shotbot perch-cam perch-vm libperch-text.so
libperch-text.so: perch-text.c
	$(CC) $(CFLAGS) -fPIC -shared -o $@ $< -ldl -lpthread
perch-eye: perch-eye.c perch-box.h perch-core.h
	$(CC) $(CFLAGS) -o $@ $< -lX11 -lXext -lXdamage -lXfixes -lm
perch-feed: perch-feed.c
	$(CC) $(CFLAGS) -o $@ $< -lX11
perch-tap: perch-tap.c perch-box.h
	$(CC) $(CFLAGS) -o $@ $<
perch-box: perch-box.c perch-box.h
	$(CC) $(CFLAGS) -o $@ $<
perch-chip: perch-chip.c
	$(CC) $(CFLAGS) -o $@ $< -lX11
perch-hen: perch-hen.c
	$(CC) $(CFLAGS) -o $@ $< -lX11
perch-wait: perch-wait.c perch-box.h
	$(CC) $(CFLAGS) -o $@ $<
perch-crop: perch-crop.c
	$(CC) $(CFLAGS) -o $@ $< -lX11 -lz
perch-scene: perch-scene.c
	$(CC) $(CFLAGS) -o $@ $< -lX11
perch-shotbot: perch-shotbot.c
	$(CC) $(CFLAGS) -o $@ $< -lX11 -lz
perch-cam: perch-cam.c perch-box.h
	$(CC) $(CFLAGS) -o $@ $<
clean:
	rm -f perch-eye perch-feed perch-tap perch-box perch-chip perch-hen perch-wait perch-crop perch-scene perch-shotbot perch-cam perch-vm; rm -rf dist
perch-vm: perch-vm.c perch-core.h perch-box.h
	$(CC) $(CFLAGS) -o $@ $< $(shell pkg-config --cflags --libs gio-2.0 gio-unix-2.0) -lm

# ---- the product: install, and a .deb of the VM eye (binaries only, stripped; no source goes out) ----
VERSION ?= $(shell cat VERSION)
PREFIX ?= /usr/local
PRODUCT_BIN = perch-vm perch-box perch-wait perch-crop
PRODUCT_SCRIPTS = perch-vmrun perch-act perch-web perch-vmlook perch-report perch-look perch-mcp perch-settings perch-run perch-video perch-sound
install: $(PRODUCT_BIN)
	install -d $(DESTDIR)$(PREFIX)/bin $(DESTDIR)$(PREFIX)/share/perch/guest $(DESTDIR)$(PREFIX)/lib/perch
	install -m 644 perch_read.py perch_read_seed.json $(DESTDIR)$(PREFIX)/lib/perch/
	install -m 644 perch_settings.py perch_sound.py perch_tag.py perch_play.py perch_cuts.py perch_voices.py perch_speaker.py $(DESTDIR)$(PREFIX)/lib/perch/
	install -m 644 libperch-text.so $(DESTDIR)$(PREFIX)/lib/perch/
	install -d $(DESTDIR)$(PREFIX)/share/perch/fonts
	install -m 644 demo/inbox.html $(DESTDIR)$(PREFIX)/share/perch/
	install -m 644 site/assets/fonts/AtkinsonHyperlegible-Regular.ttf site/assets/fonts/AtkinsonHyperlegible-Bold.ttf site/assets/fonts/DejaVuSansMono.ttf $(DESTDIR)$(PREFIX)/share/perch/fonts/
	install -s -m 755 $(PRODUCT_BIN) $(DESTDIR)$(PREFIX)/bin/
	install -m 755 $(PRODUCT_SCRIPTS) $(DESTDIR)$(PREFIX)/bin/
	install -m 755 perch-guest $(DESTDIR)$(PREFIX)/share/perch/guest/
	install -m 644 perch-guest.ps1 vm-win/perch-guest-task.ps1 $(DESTDIR)$(PREFIX)/share/perch/guest/
	install -m 644 LICENSE LICENSE.md README.md $(DESTDIR)$(PREFIX)/share/perch/
deb: $(PRODUCT_BIN)
	rm -rf dist/pkg && mkdir -p dist/pkg/DEBIAN
	$(MAKE) install DESTDIR=$(CURDIR)/dist/pkg PREFIX=/usr
	printf 'Package: perch\nVersion: $(VERSION)\nArchitecture: amd64\nMaintainer: Mike J Hebblethwaite\nDepends: qemu-system-x86 (>= 1:8.0), dbus-daemon | dbus, libglib2.0-0t64 | libglib2.0-0, python3, python3-pyatspi, gir1.2-atspi-2.0, gir1.2-webkit2-4.1, xvfb\nSection: misc\nPriority: optional\nRecommends: tesseract-ocr\nDescription: Perch: a virtual machine screen for bots, without screenshots\n The guest screen comes straight from the hypervisor as changed boxes and\n raw vectors; text from the guest helper; keys and mouse back the same way.\n' > dist/pkg/DEBIAN/control
	printf 'Copyright (c) 2026 Mike J Hebblethwaite. GNU AGPL-3.0 or a commercial licence: see /usr/share/perch/LICENSE.md\n' > dist/pkg/DEBIAN/copyright
	dpkg-deb --root-owner-group --build dist/pkg dist/perch_$(VERSION)_amd64.deb

test:			# real checks on real videos (skipped when the test video is not here)
	./tests/test_video_pass.py
