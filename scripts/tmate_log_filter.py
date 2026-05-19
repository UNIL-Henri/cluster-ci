#!/usr/bin/env python3
"""Filter for cleaning tmux/tmate terminal escape sequences from SSH output.

Implements a lightweight virtual terminal emulator (160x24) with robust
decoding and exception handling, alongside a sliding window history filter
to guarantee flawless real-time extraction of clean, non-duplicated log lines.
"""
import sys
import re
import codecs
import traceback

WIDTH = 160
HEIGHT = 24

# Screen buffer and cursor state
grid = [[" " for _ in range(WIDTH)] for _ in range(HEIGHT)]
x, y = 0, 0

state = "NORMAL"
csi_params = ""

# Sliding window of recently printed lines to filter out tmux redraw duplicates
recent_printed = []
MAX_RECENT = 100

def scroll_up():
    global grid
    # Get the line that is about to scroll out of view (the top line)
    top_line = "".join(grid[0]).rstrip()
    # Scroll the grid
    grid = grid[1:] + [[" " for _ in range(WIDTH)]]
    print_line(top_line)

def print_line(line):
    if not line:
        return
    line = line.strip()
    if not line:
        return

    # Skip tmux status bar lines (e.g. '0:bash*   ...')
    if re.match(r"^\d+:.*\*\s", line) or "bash*" in line:
        return
    # Skip script header/footer and SSH connection status messages
    if line.startswith("Script ") and ("started" in line or "done" in line):
        return
    if "Connection to" in line and "closed" in line:
        return
    if "[server exited]" in line or "[lost server]" in line:
        return
    if "size 80x23 from a smaller client" in line:
        return
    
    # Skip DVC progress bar fragments and artifacts
    if line == "!" or line.startswith("! ") or line.startswith("Checking out"):
        return
    if "file/s]" in line or "files/s]" in line or "B/s]" in line:
        return
    if re.match(r"^Checking out .+:\s+\d+%", line):
        return

    # Filter out duplicates using our sliding window
    if line in recent_printed:
        return

    # Save to sliding window
    recent_printed.append(line)
    if len(recent_printed) > MAX_RECENT:
        recent_printed.pop(0)

    print(line, flush=True)

def apply_csi(cmd, params):
    global x, y, grid
    parts = params.split(";")
    nums = []
    for p in parts:
        p_clean = "".join(c for c in p if c.isdigit())
        if p_clean:
            nums.append(int(p_clean))
        else:
            nums.append(0)

    if cmd in ("H", "f"):  # Cursor Position
        ny = nums[0] if len(nums) > 0 and nums[0] > 0 else 1
        nx = nums[1] if len(nums) > 1 and nums[1] > 0 else 1
        y = min(HEIGHT - 1, max(0, ny - 1))
        x = min(WIDTH - 1, max(0, nx - 1))
    elif cmd == "A":  # Cursor Up
        n = nums[0] if len(nums) > 0 and nums[0] > 0 else 1
        y = max(0, y - n)
    elif cmd == "B":  # Cursor Down
        n = nums[0] if len(nums) > 0 and nums[0] > 0 else 1
        y = min(HEIGHT - 1, y + n)
    elif cmd == "C":  # Cursor Forward
        n = nums[0] if len(nums) > 0 and nums[0] > 0 else 1
        x = min(WIDTH - 1, x + n)
    elif cmd == "D":  # Cursor Backward
        n = nums[0] if len(nums) > 0 and nums[0] > 0 else 1
        x = max(0, x - n)
    elif cmd == "K":  # Erase in Line
        mode = nums[0] if len(nums) > 0 else 0
        if mode == 0:  # Erase from cursor to end of line
            for i in range(x, WIDTH):
                grid[y][i] = " "
        elif mode == 1:  # Erase from start of line to cursor
            for i in range(0, min(x + 1, WIDTH)):
                grid[y][i] = " "
        elif mode == 2:  # Erase entire line
            grid[y] = [" " for _ in range(WIDTH)]
    elif cmd == "J":  # Erase in Display
        mode = nums[0] if len(nums) > 0 else 0
        if mode == 2:  # Clear entire screen
            grid = [[" " for _ in range(WIDTH)] for _ in range(HEIGHT)]
            x, y = 0, 0
    elif cmd == "S":  # Scroll Up
        n = nums[0] if len(nums) > 0 and nums[0] > 0 else 1
        for _ in range(n):
            scroll_up()

def main():
    global x, y, state, csi_params
    
    # Robust UTF-8 reader that replaces invalid bytes instead of crashing
    try:
        reader = codecs.getreader("utf-8")(sys.stdin.buffer, errors="replace")
    except Exception as e:
        with open("/tmp/tmate_filter_debug.log", "w") as f:
            f.write(f"Failed to initialize reader: {e}\n")
            traceback.print_exc(file=f)
        return

    try:
        while True:
            char = reader.read(1)
            if not char:
                break
                
            if state == "NORMAL":
                if char == "\x1b":
                    state = "ESC"
                elif char == "\n":
                    y += 1
                    if y >= HEIGHT:
                        scroll_up()
                        y = HEIGHT - 1
                elif char == "\r":
                    x = 0
                elif char == "\b":
                    x = max(0, x - 1)
                elif char == "\t":
                    # Tab stop every 8 spaces
                    x = (x + 8) & ~7
                    if x >= WIDTH:
                        x = WIDTH - 1
                elif ord(char) >= 32:
                    if 0 <= y < HEIGHT and 0 <= x < WIDTH:
                        grid[y][x] = char
                        x += 1
                        if x >= WIDTH:
                            x = 0
                            y += 1
                            if y >= HEIGHT:
                                scroll_up()
                                y = HEIGHT - 1

            elif state == "ESC":
                if char == "[":
                    state = "CSI"
                    csi_params = ""
                elif char in "()":  # Character set designators
                    state = "CHARSET"
                else:
                    state = "NORMAL"

            elif state == "CHARSET":
                state = "NORMAL"

            elif state == "CSI":
                if "0" <= char <= "9" or char in ";?":
                    csi_params += char
                else:
                    apply_csi(char, csi_params)
                    state = "NORMAL"
    except Exception as e:
        with open("/tmp/tmate_filter_debug.log", "w") as f:
            f.write(f"Exception during character loop: {e}\n")
            traceback.print_exc(file=f)

    # End of stream: dump all remaining lines in the grid that contain text
    try:
        for r in range(HEIGHT):
            line = "".join(grid[r]).rstrip()
            print_line(line)
    except Exception as e:
        with open("/tmp/tmate_filter_debug.log", "a") as f:
            f.write(f"Exception during final dump: {e}\n")
            traceback.print_exc(file=f)

if __name__ == "__main__":
    main()
