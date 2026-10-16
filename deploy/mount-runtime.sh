#!/bin/bash
set -euo pipefail
ROOT=/share/xdq/Optinfo/runtime
test -f "$ROOT/etc/os-release"
mkdir -p "$ROOT/proc" "$ROOT/harness"
mountpoint -q "$ROOT/proc" || mount -t proc -o nosuid,nodev,noexec proc "$ROOT/proc"
mountpoint -q "$ROOT/harness" || mount --bind '/share/xdq/DeepSeek Harness' "$ROOT/harness"
cp /etc/resolv.conf "$ROOT/etc/resolv.conf"
