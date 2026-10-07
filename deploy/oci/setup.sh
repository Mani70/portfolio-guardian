#!/usr/bin/env bash
# One-time server setup for Oracle Linux 9 (aarch64 or x86_64) on OCI. Safe to run again.
#
#   bash ~/portfolio-guardian/deploy/oci/setup.sh
#
# Run as the normal login user (opc); it uses sudo where needed. It does NOT place any orders:
# at the end it only verifies the token and runs the test suite.
set -euo pipefail

APP="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ME="$(id -un)"
step() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m!! %s\033[0m\n' "$*"; }

[ -f "$APP/requirements.txt" ] || { echo "Can't find the project at $APP"; exit 1; }
[ "$ME" != "root" ] || { echo "Run this as opc, not root (it calls sudo itself)."; exit 1; }
echo "Project: $APP   user: $ME"

step "1/10 Grow the root filesystem to the full boot volume"
before=$(df -h / | awk 'NR==2{print $2}')
if [ -x /usr/libexec/oci-growfs ]; then
  sudo /usr/libexec/oci-growfs -y >/dev/null 2>&1 || warn "oci-growfs reported an error (often just 'nothing to grow')"
else
  warn "oci-growfs not found; skipping (the disk still works, just smaller)"
fi
echo "Root filesystem: $before -> $(df -h / | awk 'NR==2{print $2}')"

step "2/10 Time zone Asia/Kolkata and clock sync (TOTP login needs an accurate clock)"
sudo timedatectl set-timezone Asia/Kolkata
sudo dnf -y -q install chrony
sudo systemctl enable --now chronyd
sleep 2
chronyc tracking | grep -E "Reference ID|System time|Leap status" || true

step "3/10 System updates (a few minutes on a fresh image)"
sudo dnf -y -q upgrade

step "4/10 Packages: Python 3.12, cron, logrotate, firewall, auto security updates"
sudo dnf -y -q install python3.12 python3.12-pip cronie logrotate firewalld dnf-automatic tar gzip which
sudo dnf -y -q install oracle-epel-release-el9 || true
sudo dnf config-manager --enable ol9_developer_EPEL >/dev/null 2>&1 || true
sudo dnf -y -q install fail2ban || warn "fail2ban not installed (EPEL unavailable?); continuing"

step "5/10 Firewall: SSH only; fail2ban bans repeated SSH login failures"
sudo systemctl enable --now firewalld
sudo firewall-cmd -q --permanent --add-service=ssh
for svc in cockpit dhcpv6-client; do
  sudo firewall-cmd -q --permanent --remove-service="$svc" 2>/dev/null || true
done
sudo firewall-cmd -q --reload
echo "Open services: $(sudo firewall-cmd --list-services)"
if rpm -q fail2ban >/dev/null 2>&1; then
  sudo tee /etc/fail2ban/jail.d/sshd.local >/dev/null <<'EOF'
[sshd]
enabled  = true
backend  = systemd
maxretry = 5
findtime = 10m
bantime  = 1h
EOF
  sudo systemctl enable --now fail2ban
  sudo systemctl restart fail2ban
fi

step "6/10 Automatic SECURITY updates, Saturdays 03:30 only (never during market hours)"
sudo sed -i -E 's/^upgrade_type *=.*/upgrade_type = security/; s/^apply_updates *=.*/apply_updates = yes/' \
  /etc/dnf/automatic.conf
sudo mkdir -p /etc/systemd/system/dnf-automatic.timer.d
sudo tee /etc/systemd/system/dnf-automatic.timer.d/weekend.conf >/dev/null <<'EOF'
[Timer]
OnCalendar=
OnCalendar=Sat *-*-* 03:30:00
RandomizedDelaySec=0
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now dnf-automatic.timer

step "7/10 Python virtual environment and packages"
if [ ! -x "$APP/.venv/bin/python" ] || ! "$APP/.venv/bin/python" -c 'import sys; assert sys.version_info >= (3, 12)' 2>/dev/null; then
  rm -rf "$APP/.venv"
  python3.12 -m venv "$APP/.venv"
fi
"$APP/.venv/bin/python" -m pip install -q --upgrade pip
"$APP/.venv/bin/python" -m pip install -q -r "$APP/requirements.txt" -r "$APP/deploy/oci/requirements-server.txt"
"$APP/.venv/bin/python" -c 'import pandas, numpy, yaml, pyotp, requests; print("pandas", pandas.__version__, "numpy", numpy.__version__)'

step "8/10 Files and permissions"
cd "$APP"
mkdir -p logs/cron backups trader/state cache
# Windows editors can add CRLF line endings, which break bash scripts
sed -i 's/\r$//' deploy/oci/*.sh deploy/oci/crontab
chmod +x deploy/oci/*.sh
chmod 700 "$APP" trader/state backups
for f in .env .token_cache.json trader.yaml config.yaml; do [ -f "$f" ] && chmod 600 "$f"; done
[ -f .env ] || warn ".env is missing: copy it from your laptop before the first job runs"
[ -f trader.yaml ] || warn "trader.yaml is missing: the trader will fall back to defaults"
[ -f config.yaml ] || warn "config.yaml is missing: the guardian will use config.example.yaml"
sudo tee /etc/logrotate.d/portfolio-guardian >/dev/null <<EOF
$APP/logs/*.log $APP/logs/cron/*.log {
    su $ME $ME
    weekly
    rotate 12
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
}
EOF
sudo logrotate -d /etc/logrotate.d/portfolio-guardian >/dev/null 2>&1 && echo "logrotate config OK"

step "9/10 Schedule (cron, weekdays IST)"
sudo systemctl enable --now crond
sed "s#__APP__#$APP#g" deploy/oci/crontab > /tmp/pg-crontab.$$
if crontab -l >/dev/null 2>&1; then crontab -l > "backups/crontab.before-setup.$(date +%Y%m%d%H%M%S)"; fi
crontab /tmp/pg-crontab.$$
rm -f /tmp/pg-crontab.$$
crontab -l | grep -v '^#' | grep -v '^$'

step "10/10 Checks: tests, token, Telegram (no orders are placed)"
"$APP/.venv/bin/python" -m pytest -q tests 2>&1 | tail -3 || warn "some tests failed: send me the output"
if [ -f .env ]; then
  "$APP/.venv/bin/python" -m guardian.main --check-auth || warn "token check failed (see above)"
  "$APP/deploy/oci/job.sh" health && echo "Health check sent: look for the message in Telegram."
fi

if command -v needs-restarting >/dev/null && ! sudo needs-restarting -r >/dev/null 2>&1; then
  warn "A kernel/system update needs a reboot. Run:  sudo reboot   (then log in again; cron starts by itself)"
fi
date > "$APP/.setup-done"
printf '\n\033[1;32mSetup finished.\033[0m Logs: %s/logs/cron/   Schedule: crontab -l\n' "$APP"
