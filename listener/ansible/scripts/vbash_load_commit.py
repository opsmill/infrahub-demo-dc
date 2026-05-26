#!/usr/bin/env python3
"""
Apply a VyOS config.boot via vbash: configure; load <file>; commit; save.

VyOS 1.5-Stream's `show version` errors on mokutil in containerlab, which
breaks vyos.vyos collection's network_os_version detection. This script
sidesteps the collection with a raw interactive SSH session.
"""
from __future__ import annotations
import os
import re
import sys
import time
import paramiko


ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")
# VyOS vbash prompts: operational mode ends with "$ ", config mode ends with "# ".
PROMPT_RE = re.compile(r"(admin@[^#\n$]+[#$] ?)\Z")


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def read_until_prompt(chan, timeout_seconds: float = 60.0) -> str:
    """Drain the channel until a vbash prompt appears at the tail, or timeout.

    A short fixed sleep (like `time.sleep(2)`) is unreliable when the server-
    side command takes variable time (e.g. `source` over 96 set commands).
    Wait for the prompt to reappear before sending the next command.
    """
    deadline = time.monotonic() + timeout_seconds
    buf = ""
    while time.monotonic() < deadline:
        if chan.recv_ready():
            chunk = chan.recv(65535).decode("utf-8", errors="replace")
            buf += strip_ansi(chunk)
            if PROMPT_RE.search(buf):
                return buf
        else:
            time.sleep(0.1)
    return buf  # timed out; caller decides what to do


def send_and_wait(chan, cmd: str, timeout_seconds: float = 60.0) -> str:
    chan.send(cmd + "\n")
    return read_until_prompt(chan, timeout_seconds=timeout_seconds)


def run(host: str, user: str, boot_path: str) -> int:
    password = os.environ.get("VYOS_SSH_PASS") or ""
    if not password:
        print("VYOS_SSH_PASS env var required", file=sys.stderr)
        return 2
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host, username=user, password=password,
                   look_for_keys=False, allow_agent=False, timeout=15)

    # The transform renders a full VyOS config (system, ssh, eth0, firewall),
    # but on this containerlab fw1 only the firewall ruleset should change.
    # Re-applying `set interfaces ethernet eth0 ...` fails on commit because
    # the VyOS container lacks the iproute2 bits needed to re-init eth0's
    # IPv6 link-local. So filter the artifact to firewall lines only.
    sftp = client.open_sftp()
    try:
        with sftp.open(boot_path, "r") as fh:
            all_lines = fh.read().decode("utf-8").splitlines()
        firewall_lines = [ln for ln in all_lines if ln.startswith("set firewall ") or ln.startswith("delete firewall ")]
        filtered_path = boot_path + ".firewall"
        with sftp.open(filtered_path, "w") as fh:
            fh.write(("\n".join(firewall_lines) + "\n").encode("utf-8"))
    finally:
        sftp.close()

    chan = client.invoke_shell()
    # Drain the login banner
    read_until_prompt(chan, timeout_seconds=10.0)

    # `load` wants declarative curly-brace config; we have imperative `set`
    # commands. Use `source` inside configure mode, after wiping ALLOW-WEB
    # so the deploy is a true replacement of the ruleset (FR-017).
    #
    # Per-command timeouts: source can take a while (24 rules x 4 set
    # commands = 96 statements); commit triggers VyOS to re-apply interface
    # and firewall config; both can take >10s on a slow host.
    steps = [
        ("configure", 15.0),
        ("delete firewall ipv4 name ALLOW-WEB", 15.0),
        (f"source {filtered_path}", 120.0),
        ("commit", 60.0),
        ("save", 30.0),
        ("exit", 10.0),
    ]
    output = ""
    for cmd, timeout in steps:
        output += send_and_wait(chan, cmd, timeout_seconds=timeout)
    client.close()
    print(output)
    lowered = output.lower()
    bad_markers = ("error:", "failed to commit")
    for m in bad_markers:
        if m in lowered:
            print(f"detected error marker: {m}", file=sys.stderr)
            return 3
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print(f"usage: {sys.argv[0]} <host> <user> <boot_path>", file=sys.stderr)
        sys.exit(2)
    sys.exit(run(sys.argv[1], sys.argv[2], sys.argv[3]))
