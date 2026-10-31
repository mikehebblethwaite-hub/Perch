CFLAGS ?= -O2 -Wall -Wextra
all: coop-perch-eye coop-perch-feed coop-perch-tap coop-perch-box
coop-perch-eye: coop-perch-eye.c coop-perch-box.h
	$(CC) $(CFLAGS) -o $@ $< -lX11 -lXext -lXdamage -lXfixes -lm
coop-perch-feed: coop-perch-feed.c
	$(CC) $(CFLAGS) -o $@ $< -lX11
coop-perch-tap: coop-perch-tap.c coop-perch-box.h
	$(CC) $(CFLAGS) -o $@ $<
coop-perch-box: coop-perch-box.c coop-perch-box.h
	$(CC) $(CFLAGS) -o $@ $<
clean:
	rm -f coop-perch-eye coop-perch-feed coop-perch-tap coop-perch-box
