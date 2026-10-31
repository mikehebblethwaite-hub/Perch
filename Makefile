CFLAGS ?= -O2 -Wall -Wextra
all: coop-perch-eye coop-perch-feed
coop-perch-eye: coop-perch-eye.c
	$(CC) $(CFLAGS) -o $@ $< -lX11 -lXext -lXdamage -lXfixes -lm
coop-perch-feed: coop-perch-feed.c
	$(CC) $(CFLAGS) -o $@ $< -lX11
clean:
	rm -f coop-perch-eye coop-perch-feed
