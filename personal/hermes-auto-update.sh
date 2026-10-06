#!/usr/bin/env bash
# Hermes auto-update watchdog.
#
# Designed to run via `hermes cron --no-agent --script hermes-auto-update.sh`.
# Contract for that mode: empty stdout == silent (no notification).
# So we print NOTHING when already up to date, and a short summary only
# when an update was actually applied.
#
# Update strategy: --check first (cheap, no writes); only if a newer version
# exists do we run `hermes update --yes --backup` (backup before each upgrade
# so a bad release can be rolled back via `hermes import`).
set -u

HERMES="/Users/max/.local/bin/hermes"
REPO="/Users/max/.hermes/hermes-agent"

# Local source patches live in this dir. Each one is idempotent, prints nothing
# when already in place, and retires itself once upstream ships the same fix --
# so the silent-stdout contract holds and a stale patch cannot linger unnoticed.
PY="/usr/bin/python3"
PATCH_FEISHU_INDENT="/Users/max/.hermes/scripts/feishu-indent-patch.py"
FEISHU_ADAPTER="$REPO/plugins/platforms/feishu/adapter.py"
PYPI_PROBE="https://files.pythonhosted.org/packages/0f/73/bb1bc2529f852e7bf64a2dec885e89ff9f5cc7bbf6c9340eed30ff2c69c5/ruamel.yaml-0.18.16-py3-none-any.whl"

running_gateway_sha() {
  "$PY" -c '
import json, os
from pathlib import Path
try:
    state = json.loads(Path("/Users/max/.hermes/gateway_state.json").read_text())
    pid = state.get("pid")
    if state.get("gateway_state") == "running" and isinstance(pid, int):
        os.kill(pid, 0)
        print(state.get("code_sha") or "")
except (OSError, ValueError, TypeError):
    pass
' 2>/dev/null
}

apply_local_patches() {
  [ -f "$PATCH_FEISHU_INDENT" ] || return 0
  if [ ! -x "$PY" ]; then
    echo "Cannot apply Feishu patch: $PY is unavailable" >&2
    return 1
  fi
  "$PY" "$PATCH_FEISHU_INDENT" apply 2>&1
}

# 1) Is there anything to do?
check="$("$HERMES" update --check 2>&1)"
check_status=$?
if [ "$check_status" -ne 0 ]; then
  echo "Hermes auto-update check failed (exit ${check_status})"
  printf '%s\n' "$check"
  exit "$check_status"
fi
if printf '%s' "$check" | grep -qiE "already up to date|up to date|no update"; then
  checkout_sha="$(git -C "$REPO" rev-parse HEAD 2>/dev/null)"
  gateway_sha="$(running_gateway_sha)"
  if [ -n "$checkout_sha" ] && [ -n "$gateway_sha" ] && [ "$checkout_sha" != "$gateway_sha" ]; then
    echo "Gateway is still on ${gateway_sha:0:10}; repairing the update to ${checkout_sha:0:10}."
  else
    # No upgrade, but re-assert patches after any manual update.
    patch_before="$(/usr/bin/shasum -a 256 "$FEISHU_ADAPTER" 2>/dev/null)"
    patch_log="$(apply_local_patches)"
    patch_status=$?
    if [ "$patch_status" -ne 0 ]; then
      printf '%s\n' "$patch_log"
      exit "$patch_status"
    fi
    patch_after="$(/usr/bin/shasum -a 256 "$FEISHU_ADAPTER" 2>/dev/null)"
    if [ -n "$patch_before" ] && [ "$patch_before" != "$patch_after" ]; then
      echo "Feishu adapter patch changed; restarting the gateway to load it."
      "$HERMES" gateway restart
      exit $?
    fi
    [ -z "$patch_log" ] || printf '%s\n' "$patch_log"
    exit 0
  fi
elif ! printf '%s' "$check" | grep -qi 'update available'; then
  echo "Hermes auto-update check returned an unrecognized result; no update started."
  printf '%s\n' "$check"
  exit 1
fi

# A failed package download previously left new source beside an old gateway.
# Skip this run if the package host is unreachable, before changing the checkout.
if ! /usr/bin/curl -fsSI --connect-timeout 5 --max-time 15 "$PYPI_PROBE" >/dev/null 2>&1; then
  echo "Hermes auto-update skipped: Python package host is unreachable."
  exit 1
fi

# 2) An update is available -> apply it (unattended, with pre-update backup).
before="$("$HERMES" --version 2>&1 | head -1)"
log="$("$HERMES" update --yes --backup 2>&1)"
status=$?
after="$("$HERMES" --version 2>&1 | head -1)"

# 2b) The upgrade overwrote the install tree, so re-graft local patches.
patch_log="$(apply_local_patches)"
patch_status=$?

# 3) Report (this output is delivered as the notification).
echo "Hermes auto-update ran (exit ${status})"
echo "before: ${before}"
echo "after:  ${after}"
if [ -n "${patch_log}" ]; then
  echo "----- local patches -----"
  printf '%s\n' "${patch_log}"
fi
if [ "$patch_status" -ne 0 ]; then
  echo "⚠ Local patch failed (exit ${patch_status})"
fi
if [ "$status" -eq 0 ]; then
  echo "note: Hermes handles the gateway restart; a cron-owned gateway may finish restarting after this job exits."
else
  echo "warning: the checkout may have changed without a gateway restart; the next run will detect and repair code skew."
fi
echo "----- update log (tail) -----"
printf '%s\n' "$log" | tail -20
if [ "$status" -ne 0 ]; then
  exit "$status"
fi
exit "$patch_status"
