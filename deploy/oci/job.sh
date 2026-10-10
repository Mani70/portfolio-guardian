#!/usr/bin/env bash
# Runs one scheduled job: logs to logs/cron/<job>.log, never two copies of the same job at once,
# a time limit per job, and a Telegram alert if the job fails or times out.
#
#   deploy/oci/job.sh intraday|watch|swing-check|swing-plan|guardian|health|backup|report|universe|holidays|autodeploy|retest|insights|fo-paper|reel|reboot
set -uo pipefail

APP="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$APP" || exit 1
export PYTHONUTF8=1 TZ=Asia/Kolkata
PY="$APP/.venv/bin/python"
job="${1:-}"

case "$job" in
  intraday)    limit=7h;  cmd=(-m trader.run intraday) ;;
  watch)       limit=7h;  cmd=(-m trader.run watch) ;;
  swing-check) limit=30m; cmd=(-m trader.run swing-check) ;;
  swing-plan)  limit=30m; cmd=(-m trader.run swing-plan) ;;
  guardian)    limit=30m; cmd=(-m guardian.main) ;;
  health)      limit=5m;  cmd=(deploy/oci/health.py) ;;
  reboot)      limit=5m;  cmd=(deploy/oci/health.py --reboot) ;;
  backup)      limit=15m; cmd=(deploy/oci/backup.py) ;;
  report)      limit=10m; cmd=(-m trader.run report) ;;
  universe)    limit=5m;  cmd=(-m trader.run universe) ;;
  holidays)    limit=5m;  cmd=(-m trader.run holidays) ;;
  autodeploy)  limit=40m; cmd=(deploy/oci/autodeploy.py) ;;
  retest)      limit=10h; cmd=(-m trader.run retest) ;;
  insights)    limit=60m; cmd=(-m trader.run insights) ;;
  fo-paper)    limit=20m; cmd=(-m trader.run fo-paper) ;;
  reel)        limit=30m; cmd=(-m trader.run reel) ;;
  *) echo "usage: $0 intraday|watch|swing-check|swing-plan|guardian|health|backup|report|universe|holidays|autodeploy|retest|insights|fo-paper|reel|reboot" >&2; exit 2 ;;
esac

mkdir -p logs/cron
log="logs/cron/$job.log"
stamp() { date '+%Y-%m-%d %H:%M:%S'; }

exec 9>"logs/cron/.$job.lock"
if ! flock -n 9; then
  echo "$(stamp) $job is still running from earlier; this start was skipped" >> "$log"
  exit 0
fi

echo "===== $(stamp) start $job" >> "$log"
timeout --kill-after=60 "$limit" "$PY" "${cmd[@]}" >> "$log" 2>&1
rc=$?
echo "===== $(stamp) end $job rc=$rc" >> "$log"

if [ "$rc" -ne 0 ]; then
  why="exit code $rc"
  [ "$rc" -eq 124 ] && why="stopped after the $limit time limit"
  "$PY" deploy/oci/alert.py "Server job '$job' failed ($why)" "$log" >> "$log" 2>&1 || true
fi
exit "$rc"
