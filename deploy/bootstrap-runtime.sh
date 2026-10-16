#!/bin/bash
set -euo pipefail
BASE=/share/xdq/Optinfo
ROOT="$BASE/runtime"
mkdir -p "$BASE/downloads" "$ROOT" "/share/xdq/DeepSeek Harness"
cd "$BASE/downloads"
curl -fsSL https://cdimage.ubuntu.com/ubuntu-base/releases/22.04.5/release/SHA256SUMS -o SHA256SUMS
curl -fL https://cdimage.ubuntu.com/ubuntu-base/releases/22.04.5/release/ubuntu-base-22.04.5-base-amd64.tar.gz -o ubuntu-base.tar.gz
expected=$(awk '/ubuntu-base-22.04.5-base-amd64.tar.gz$/ { print $1 }' SHA256SUMS)
test -n "$expected"
printf '%s  ubuntu-base.tar.gz\n' "$expected" | sha256sum -c -
if [ ! -e "$ROOT/etc/os-release" ]; then
  tar -xzf ubuntu-base.tar.gz -C "$ROOT" --numeric-owner
fi
cp /etc/resolv.conf "$ROOT/etc/resolv.conf"
mkdir -p "$ROOT/proc" "$ROOT/dev" "$ROOT/harness" "$ROOT/app" "$ROOT/data" "$ROOT/config"
test -c "$ROOT/dev/null" || mknod -m 666 "$ROOT/dev/null" c 1 3
test -c "$ROOT/dev/zero" || mknod -m 666 "$ROOT/dev/zero" c 1 5
test -c "$ROOT/dev/random" || mknod -m 666 "$ROOT/dev/random" c 1 8
test -c "$ROOT/dev/urandom" || mknod -m 666 "$ROOT/dev/urandom" c 1 9
mountpoint -q "$ROOT/proc" || mount -t proc -o nosuid,nodev,noexec proc "$ROOT/proc"
mountpoint -q "$ROOT/harness" || mount --bind '/share/xdq/DeepSeek Harness' "$ROOT/harness"
printf '#!/bin/sh\nexit 101\n' > "$ROOT/usr/sbin/policy-rc.d"
chmod 755 "$ROOT/usr/sbin/policy-rc.d"
chroot "$ROOT" /bin/bash -c 'set -e; export DEBIAN_FRONTEND=noninteractive; apt-get update -qq; apt-get install -y --no-install-recommends python3 python3-venv python3-pip git ca-certificates tzdata; python3 -m venv /harness/venv; /harness/venv/bin/pip install --no-cache-dir deepseek-harness-sdk==0.1.5rc1'
id optinfo >/dev/null 2>&1 || useradd --system --no-create-home --home-dir /share/xdq/Optinfo --shell /sbin/nologin optinfo
uid=$(id -u optinfo); gid=$(id -g optinfo)
grep -q '^optinfo:' "$ROOT/etc/passwd" || printf 'optinfo:x:%s:%s:Optinfo:/data:/usr/sbin/nologin\n' "$uid" "$gid" >> "$ROOT/etc/passwd"
grep -q '^optinfo:' "$ROOT/etc/group" || printf 'optinfo:x:%s:\n' "$gid" >> "$ROOT/etc/group"
chown -R "$uid:$gid" "$ROOT/data" "$ROOT/config" '/share/xdq/DeepSeek Harness'
chmod 700 "$ROOT/config"
chroot --userspec="$uid:$gid" "$ROOT" /harness/venv/bin/python -c 'import deepseek_harness; print("Harness Python import OK")'
printf 'Runtime ready; service uid=%s gid=%s\n' "$uid" "$gid"
