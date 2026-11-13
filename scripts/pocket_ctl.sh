#!/data/data/com.termux/files/usr/bin/sh
# Phone-side PocketLLM service manager (runs inside Termux, no root required).
# Usage: pocket_ctl.sh [server|chat|stop|status|tunnel [token]|tunnel-stop]

export PREFIX="${PREFIX:-/data/data/com.termux/files/usr}"
export HOME="${HOME:-/data/data/com.termux/files/home}"
export PATH="$PREFIX/bin:$PATH"
export TMPDIR="${TMPDIR:-$PREFIX/tmp}"

CONF="$HOME/.pocket_config.env"
if [ -f "$CONF" ]; then
  # shellcheck disable=SC1090
  . "$CONF"
fi

LLAMA_DIR="${LLAMA_DIR:-$HOME/llama}"
MODEL="${MODEL:-}"
CORE_MASK="${CORE_MASK:-}"
THREADS="${THREADS:-4}"
CTX="${CTX:-2048}"
PORT="${PORT:-8080}"
SERVER_LOG="$HOME/llama-server.log"
TUNNEL_LOG="$HOME/cloudflared.log"

export LD_LIBRARY_PATH="$LLAMA_DIR:$PREFIX/lib"

pin_prefix() {
  if [ -n "$CORE_MASK" ] && command -v taskset >/dev/null 2>&1; then
    if taskset "$CORE_MASK" true >/dev/null 2>&1; then
      echo "taskset $CORE_MASK"
    fi
  fi
}

run_pinned() {
  PREFIX_CMD=$(pin_prefix)
  if [ -n "$PREFIX_CMD" ]; then
    $PREFIX_CMD "$@"
  else
    "$@"
  fi
}

get_wlan_ip() {
  if command -v ip >/dev/null 2>&1; then
    ip -4 -o addr show wlan0 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -n1
  elif command -v ifconfig >/dev/null 2>&1; then
    ifconfig wlan0 2>/dev/null | awk '/inet /{print $2}' | sed 's/addr://' | head -n1
  fi
}

stop_server() {
  pkill -f "llama-server" >/dev/null 2>&1 || true
}

stop_tunnel() {
  pkill -f "cloudflared" >/dev/null 2>&1 || true
}

case "${1:-status}" in
  server)
    if [ -z "$MODEL" ] || [ ! -f "$MODEL" ]; then
      echo "ERROR: Model file not found: $MODEL" >&2
      exit 1
    fi
    stop_server
    sleep 1
    termux-wake-lock >/dev/null 2>&1 || true
    PIN_CMD=$(pin_prefix)
    nohup $PIN_CMD "$LLAMA_DIR/llama-server" -m "$MODEL" -t "$THREADS" -c "$CTX" \
      --host 0.0.0.0 --port "$PORT" </dev/null >"$SERVER_LOG" 2>&1 &
    SPID=$!

    i=0
    while [ "$i" -lt 45 ]; do
      if ! kill -0 "$SPID" 2>/dev/null; then
        echo "ERROR: llama-server exited unexpectedly. Log:" >&2
        tail -n 25 "$SERVER_LOG" >&2
        exit 1
      fi
      if command -v curl >/dev/null 2>&1; then
        if curl -sf "http://127.0.0.1:${PORT}/health" | grep -q "ok"; then
          IP=$(get_wlan_ip)
          echo "SERVER_READY pid=$SPID port=$PORT wlan_ip=${IP:-unknown}"
          exit 0
        fi
      else
        # Fallback if curl is not installed: wait briefly and verify process alive
        if [ "$i" -ge 4 ]; then
          IP=$(get_wlan_ip)
          echo "SERVER_READY pid=$SPID port=$PORT wlan_ip=${IP:-unknown}"
          exit 0
        fi
      fi
      i=$((i + 1))
      sleep 1
    done
    echo "ERROR: llama-server timed out becoming healthy. Log:" >&2
    tail -n 25 "$SERVER_LOG" >&2
    exit 1
    ;;

  chat)
    if [ -z "$MODEL" ] || [ ! -f "$MODEL" ]; then
      echo "ERROR: Model file not found: $MODEL" >&2
      exit 1
    fi
    stop_server
    sleep 0.5
    exec run_pinned "$LLAMA_DIR/llama-cli" -m "$MODEL" -t "$THREADS" -c "$CTX" \
      --temp 0.7 --repeat-penalty 1.1 \
      -sys "You are a helpful, concise assistant."
    ;;

  tunnel)
    TOKEN="${2:-}"
    if ! command -v cloudflared >/dev/null 2>&1; then
      echo "Installing cloudflared via pkg..."
      export DEBIAN_FRONTEND=noninteractive
      yes | pkg update -y -o Dpkg::Options::=--force-confnew >/dev/null 2>&1 || true
      yes | pkg install -y -o Dpkg::Options::=--force-confnew cloudflared
    fi
    stop_tunnel
    sleep 1
    termux-wake-lock >/dev/null 2>&1 || true
    : > "$TUNNEL_LOG"
    if [ -n "$TOKEN" ]; then
      nohup cloudflared tunnel --no-autoupdate run --token "$TOKEN" </dev/null >"$TUNNEL_LOG" 2>&1 &
      echo "TUNNEL_NAMED pid=$!"
      exit 0
    else
      nohup cloudflared tunnel --no-autoupdate --url "http://127.0.0.1:${PORT}" </dev/null >"$TUNNEL_LOG" 2>&1 &
      TPID=$!
      i=0
      while [ "$i" -lt 30 ]; do
        URL=$(grep -oE 'https://[a-zA-Z0-9.-]+\.trycloudflare\.com' "$TUNNEL_LOG" 2>/dev/null | head -n1)
        if [ -n "$URL" ]; then
          echo "TUNNEL_READY pid=$TPID url=$URL"
          exit 0
        fi
        if ! kill -0 "$TPID" 2>/dev/null; then
          echo "ERROR: cloudflared exited. Log:" >&2
          tail -n 20 "$TUNNEL_LOG" >&2
          exit 1
        fi
        i=$((i + 1))
        sleep 1
      done
      echo "ERROR: Timed out waiting for trycloudflare URL. Log:" >&2
      tail -n 20 "$TUNNEL_LOG" >&2
      exit 1
    fi
    ;;

  tunnel-stop)
    stop_tunnel
    echo "TUNNEL_STOPPED"
    ;;

  stop)
    stop_tunnel
    stop_server
    termux-wake-unlock >/dev/null 2>&1 || true
    echo "STOPPED"
    ;;

  status)
    echo "--- PROCESSES ---"
    ps -ef 2>/dev/null | grep -E "[l]lama-server|[c]loudflared" || echo "none"
    echo "--- IP ---"
    get_wlan_ip || true
    echo "--- MEMORY ---"
    cat /proc/meminfo | head -n 3
    ;;

  *)
    echo "Usage: $0 {server|chat|stop|status|tunnel [token]|tunnel-stop}"
    exit 1
    ;;
esac
