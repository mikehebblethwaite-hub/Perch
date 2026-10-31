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
	rm -f coop-perch-eye coop-perch-feed coop-perch-tap coop-perch-box coop-perch-chip coop-perch-hen coop-perch-wait coop-perch-crop coop-perch-scene coop-perch-shotbot coop-perch-cam coop-perch-vm
coop-perch-vm: coop-perch-vm.c coop-perch-core.h coop-perch-box.h
	$(CC) $(CFLAGS) -o $@ $< $(shell pkg-config --cflags --libs gio-2.0 gio-unix-2.0) -lm
