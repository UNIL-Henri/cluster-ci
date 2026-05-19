#!/usr/bin/env python3
"""Filter for cleaning tmux/tmate terminal escape sequences from SSH output.

Reads raw bytes from stdin, strips ANSI/VT100/tmux control sequences,
filters tmux status bar lines and connection messages, then prints
clean log lines to stdout in real-time.
"""
import sys
import re

for raw in sys.stdin.buffer:
    try:
        line = raw.decode("utf-8", errors="replace")
    except Exception:
        continue
    # Strip all ANSI/VT100 escape sequences
    line = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", line)
    line = re.sub(r"\x1b\([a-zA-Z0-9]", "", line)
    line = re.sub(r"\x1b\][^\x07]*\x07", "", line)  # OSC sequences
    line = re.sub(r"\x1b[>=<NOM78DHE]", "", line)  # mode switches
    line = re.sub(r"[\x00-\x08\x0e-\x1f]", "", line)  # control chars (keep \n \t)
    line = re.sub(r"\r", "", line)  # carriage returns
    # Skip tmux status bar lines (e.g. '0:bash*   ...')
    if re.match(r"^\d+:.*\*\s", line):
        continue
    # Skip script header/footer
    if line.startswith("Script ") and ("started" in line or "done" in line):
        continue
    # Skip connection closure messages
    if "Connection to" in line and "closed" in line:
        continue
    if "[server exited]" in line or "[lost server]" in line:
        continue
    # Skip DVC/tqdm progress bar fragments (carriage-return artifacts)
    if re.match(r"^!\s", line) or re.match(r"^!\s*$", line):
        continue
    if re.match(r"^\s*\d+%\s*\|", line):
        continue
    if "?file/s]" in line or "?files/s]" in line:
        continue
    if re.match(r"^Checking out .+:\s+\d+%", line):
        continue
    line = line.rstrip()
    if line:
        print(line, flush=True)
