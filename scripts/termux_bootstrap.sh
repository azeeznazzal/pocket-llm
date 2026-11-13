#!/data/data/com.termux/files/usr/bin/sh
# Fallback bootstrap script executed inside Termux when `adb shell run-as` is unavailable.
# Installs OpenSSH, authorizes the host SSH public key, starts sshd on :8022, and acquires wake-lock.
set -e

export DEBIAN_FRONTEND=noninteractive
APT_OPTS="-o Dpkg::Options::=--force-confnew -o Dpkg::Options::=--force-confdef"

yes | pkg update -y $APT_OPTS || true
yes | pkg install -y $APT_OPTS openssh termux-tools

if [ -f /data/local/tmp/pocket_host_key.pub ]; then
  mkdir -p "$HOME/.ssh"
  chmod 700 "$HOME/.ssh"
  PUB_KEY=$(cat /data/local/tmp/pocket_host_key.pub)
  grep -Fq "$PUB_KEY" "$HOME/.ssh/authorized_keys" 2>/dev/null || echo "$PUB_KEY" >> "$HOME/.ssh/authorized_keys"
  chmod 600 "$HOME/.ssh/authorized_keys"
fi

mkdir -p "$HOME/.termux"
touch "$HOME/.bashrc"
grep -q "sshd" "$HOME/.bashrc" 2>/dev/null || echo "pgrep -x sshd >/dev/null || sshd" >> "$HOME/.bashrc"

pgrep -x sshd >/dev/null || sshd
termux-wake-lock >/dev/null 2>&1 || true
echo "POCKET_BOOTSTRAP_OK" > "$HOME/.pocket_bootstrapped"
echo "POCKET_BOOTSTRAP_OK"
