#!/usr/bin/env python3
"""Render or serve only a public-key SSH bootstrap for the isolated Live guest.

No private keys, model configuration, arbitrary files, directory serving or SSH
connections are read or made. Execution inside the guest is a separate step.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import secrets
import shlex
import struct
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

SSH_VERSION = "1:10.2p1-ok3"


def public_key(path: Path) -> str:
    if path.suffix != ".pub" or path.is_symlink() or not path.is_file():
        raise ValueError("provide an ordinary .pub file, never a private key")
    if path.stat().st_size > 4096:
        raise ValueError("public key file is too large")
    text = path.read_text(encoding="ascii").strip()
    fields = text.split()
    if "\n" in text or len(fields) < 2 or fields[0] != "ssh-ed25519":
        raise ValueError("one unadorned ssh-ed25519 public key is required")
    try:
        raw = base64.b64decode(fields[1], validate=True)
    except ValueError:
        raise ValueError("invalid public key encoding") from None
    if (len(raw) != 51 or raw[:4] != struct.pack(">I", 11)
            or raw[4:15] != b"ssh-ed25519" or raw[15:19] != struct.pack(">I", 32)):
        raise ValueError("invalid Ed25519 public key structure")
    return " ".join(fields[:2])  # Comments cannot become shell code or identity material.


def render(key: str) -> bytes:
    return ('''#!/bin/sh
set -eu
[ "$(id -u)" = 0 ]
. /etc/os-release
[ "$ID" = openkylin ]
[ "$VERSION_ID" = 3.0 ]
[ "$(uname -m)" = aarch64 ]
grep -q 'boot=casper' /proc/cmdline
[ "$(getent passwd openkylin | cut -d: -f6)" = /home/openkylin ]
if systemctl is-active --quiet ssh || systemctl is-active --quiet ssh.socket; then
    printf '%s\\n' 'SSH is active; preserve it and inspect the existing connection instead.' >&2
    exit 1
fi
umask 077
stage=$(mktemp -d /tmp/kmb-ssh.XXXXXXXX)
policy_created=no
cleanup() {
    if [ "$policy_created" = yes ]; then
        python3 - "$policy_identity" <<'CLEANPOLICY'
import os, stat, sys
path = '/usr/sbin/policy-rc.d'
info = os.lstat(path)
if not stat.S_ISREG(info.st_mode) or f'{info.st_dev}:{info.st_ino}' != sys.argv[1]:
    raise SystemExit('package policy changed; preserving it')
os.unlink(path)
CLEANPOLICY
    fi
}
trap cleanup EXIT
trap 'exit 130' HUP INT TERM
key=''' + shlex.quote(key) + '''
printf '%s\\n' "$key" > "$stage/authorized_keys"
cat > "$stage/00-kmb-live.conf" <<'CONF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
PubkeyAuthentication yes
AuthenticationMethods publickey
AuthorizedKeysFile /home/openkylin/.ssh/authorized_keys
AuthorizedKeysCommand none
TrustedUserCAKeys none
AuthorizedPrincipalsFile none
AuthorizedPrincipalsCommand none
PubkeyAcceptedAlgorithms ssh-ed25519
AllowUsers openkylin
AllowAgentForwarding no
X11Forwarding no
GatewayPorts no
CONF
# Preserve unknown settings or authorizations; only matching owned files are reusable.
for dir in /home/openkylin/.ssh /etc/ssh /etc/ssh/sshd_config.d; do
    [ ! -L "$dir" ]
done
for pair in '/home/openkylin/.ssh/authorized_keys authorized_keys' '/etc/ssh/sshd_config.d/00-kmb-live.conf 00-kmb-live.conf'; do
    set -- $pair
    [ ! -L "$1" ]
    if [ -e "$1" ]; then cmp -s "$1" "$stage/$2"; fi
done
printf '%s\\n' 'deb https://archive.openkylin.top/openkylin huanghe main cross pty' > "$stage/official.list"
mkdir -p "$stage/lists/partial"
# No alternate repository, unauthenticated package or TLS bypass is allowed.
aptget() {
    apt-get -o Dir::Etc::sourcelist="$stage/official.list" -o Dir::Etc::sourceparts=- \\
      -o Dir::State::lists="$stage/lists" -o Acquire::Retries=2 \\
      -o Acquire::https::Timeout=30 -o Acquire::Languages=none "$@"
}
aptget update
aptget --simulate --no-install-recommends install ''' + " ".join(
        f"{name}={SSH_VERSION}" for name in
        ("openssh-server", "openssh-client", "openssh-sftp-server")) + '''
[ ! -e /usr/sbin/policy-rc.d ]
[ ! -L /usr/sbin/policy-rc.d ]
policy_identity=$(python3 - <<'CREATEPOLICY'
import os
path = '/usr/sbin/policy-rc.d'
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o755)
try:
    os.write(fd, b'#!/bin/sh\\nexit 101\\n')
    os.fchmod(fd, 0o755)
    info = os.fstat(fd)
    print(f'{info.st_dev}:{info.st_ino}')
finally:
    os.close(fd)
CREATEPOLICY
)
policy_created=yes
DEBIAN_FRONTEND=noninteractive aptget -y --no-install-recommends --no-remove install ''' + " ".join(
        f"{name}={SSH_VERSION}" for name in
        ("openssh-server", "openssh-client", "openssh-sftp-server")) + '''
install -d -m 700 -o openkylin -g openkylin /home/openkylin/.ssh
install -d -m 755 /etc/ssh/sshd_config.d
install -m 644 "$stage/00-kmb-live.conf" /etc/ssh/sshd_config.d/00-kmb-live.conf
ssh-keygen -A
/usr/sbin/sshd -t
# A plain -T does not prove what Match blocks authorize for a particular caller.
# Accept only the bounded standard config Include tree, with no active Match rules.
python3 - <<'CHECKMATCH'
from pathlib import Path
import shlex
base = Path('/etc/ssh')
pending = [base / 'sshd_config']
seen = set()
while pending:
    path = pending.pop()
    if path in seen:
        continue
    if len(seen) >= 64 or path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
        raise SystemExit('unknown or oversized SSH configuration; preserving authorization')
    seen.add(path)
    for line in path.read_text().splitlines():
        words = shlex.split(line, comments=True)
        if not words:
            continue
        # sshd accepts keyword=value as well as whitespace. Reject that alternate
        # form conservatively so Match=/Include= cannot evade the bounded scan.
        if '=' in words[0]:
            raise SystemExit('alternate SSH directive syntax requires separate review')
        if words[0].lower() == 'match':
            raise SystemExit('existing Match rules require separate review; authorization unchanged')
        if words[0].lower() == 'include':
            for pattern in words[1:]:
                candidate = Path(pattern)
                if not candidate.is_absolute():
                    candidate = base / candidate
                if candidate.parent != base / 'sshd_config.d' or candidate.suffix != '.conf':
                    raise SystemExit('nonstandard SSH Include requires separate review')
                if candidate.parent.is_symlink():
                    raise SystemExit('SSH Include directory is a symlink')
                pending.extend(candidate.parent.glob(candidate.name))
CHECKMATCH
/usr/sbin/sshd -T -C user=openkylin,host=localhost,addr=127.0.0.1 > "$stage/effective.txt"
for line in 'passwordauthentication no' 'kbdinteractiveauthentication no' 'permitrootlogin no' 'pubkeyauthentication yes' 'authenticationmethods publickey' 'authorizedkeysfile /home/openkylin/.ssh/authorized_keys' 'authorizedkeyscommand none' 'trustedusercakeys none' 'authorizedprincipalsfile none' 'authorizedprincipalscommand none' 'pubkeyacceptedalgorithms ssh-ed25519' 'allowusers openkylin' 'allowagentforwarding no' 'x11forwarding no' 'gatewayports no'; do
    grep -qxF "$line" "$stage/effective.txt"
done
install -m 600 -o openkylin -g openkylin "$stage/authorized_keys" /home/openkylin/.ssh/authorized_keys
cleanup
policy_created=no
systemctl start ssh
systemctl is-active ssh
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
printf '%s\\n' "SSH bootstrap checked; audit files: $stage"
''').encode()


def make_server(body: bytes, *, port: int = 0, route: str | None = None) -> HTTPServer:
    route = route or "/" + secrets.token_hex(16)
    if not route.startswith("/") or any(c not in "/0123456789abcdef" for c in route):
        raise ValueError("invalid generated route")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != route:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    class LimitedServer(HTTPServer):
        def get_request(self):
            connection, address = super().get_request()
            connection.settimeout(2)  # An incomplete local request cannot defeat the TTL forever.
            return connection, address

    server = LimitedServer(("127.0.0.1", port), Handler)
    server.timeout = 1
    server.route = route
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("render", "serve"))
    parser.add_argument("--public-key", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--port", type=int, default=8768)
    parser.add_argument("--ttl-seconds", type=int, default=600)
    args = parser.parse_args()
    body = render(public_key(args.public_key))
    info = {"sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body),
            "contains_private_key": False, "model_calls": 0}
    if args.action == "render":
        if args.output is None:
            parser.error("render requires --output; existing files are never replaced")
        with args.output.open("xb") as stream:
            stream.write(body)
        print(json.dumps(info))
        return
    if args.output is not None or not 1 <= args.ttl_seconds <= 1800:
        parser.error("serve accepts no output; TTL must be 1..1800 seconds")
    with make_server(body, port=args.port) as server:
        print(json.dumps({**info, "bind": "127.0.0.1", "port": server.server_port,
                          "route": server.route, "ttl_seconds": args.ttl_seconds}), flush=True)
        deadline = time.monotonic() + args.ttl_seconds
        while time.monotonic() < deadline:
            server.handle_request()


if __name__ == "__main__":
    main()
