#!/usr/bin/env bash
# Run end-to-end tests for the A (threaded client + non-threaded server)
# Usage: ./run_all.sh [port]
set -euo pipefail

PORT="${1:-1235}"
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

# Ensure launchers are executable (best effort on non-POSIX)
chmod +x Server_Non_thread/server Client_thread/client 2>/dev/null || true

# Output directories
OUT_SERVER="Server_Non_thread/outputs"
OUT_CLIENT="Client_thread/outputs"
mkdir -p "$OUT_SERVER" "$OUT_CLIENT"

# Resolve input file path (try shared/B input as fallback)
FILE="inputs/Dostoyevsky.txt"
[[ -f "$FILE" ]] || FILE="../B/Server_thread/inputs/Dostoyevsky.txt"
[[ -f "$FILE" ]] || FILE="../B/Client_Non_thread/inputs/Dostoyevsky.txt"
if [[ ! -f "$FILE" ]]; then
  echo "Input text file not found: A/inputs or B/*/inputs/Dostoyevsky.txt"
  exit 1
fi

# Clean previous outputs
rm -f \
  "$OUT_SERVER/server.out" \
  "$OUT_CLIENT/typed.out" \
  "$OUT_CLIENT/file.out" \
  "$OUT_CLIENT/dual-c1.out" \
  "$OUT_CLIENT/dual-c2.out" || true

# Try to free UDP port (Linux/macOS only)
(fuser -k "${PORT}"/udp || true) >/dev/null 2>&1 || true

# Start server in background
Server_Non_thread/server "$PORT" > "$OUT_SERVER/server.out" 2>&1 &
srv=$!
finish() { kill "$srv" 2>/dev/null || true; }
trap finish EXIT

# Wait for server to bind
if command -v ss >/dev/null 2>&1; then
  for _ in {1..50}; do ss -lun | grep -q ":$PORT " && break || sleep 0.05; done
else
  sleep 0.5
fi

# Helper: run a typed session (with PTY if available)
run_typed() {
  local text="$1" out="$2"
  if command -v script >/dev/null 2>&1; then
    printf "%s" "$text" | script -q -c "Client_thread/client 127.0.0.1 $PORT" /dev/null >"$out" 2>&1 || true
  else
    printf "%s" "$text" | Client_thread/client 127.0.0.1 "$PORT" >"$out" 2>&1 || true
  fi
}

# 1) Typed test
run_typed $'five\ntwo\nthree\nq\n' "$OUT_CLIENT/typed.out"

# 2) Single file redirect test
Client_thread/client 127.0.0.1 "$PORT" < "$FILE" > "$OUT_CLIENT/file.out" 2>&1 || true

# 3) Concurrent clients test
Client_thread/client 127.0.0.1 "$PORT" < "$FILE" > "$OUT_CLIENT/dual-c1.out" 2>&1 &
c1=$!
Client_thread/client 127.0.0.1 "$PORT" < "$FILE" > "$OUT_CLIENT/dual-c2.out" 2>&1 &
c2=$!
wait "$c1" "$c2" || true

sleep 0.2

printf "\n=== Test Summary (A) ===\n"
printf "Server log: %s\n" "$OUT_SERVER/server.out"
printf "Typed client output: %s\n" "$OUT_CLIENT/typed.out"
printf "Single-file client output: %s\n" "$OUT_CLIENT/file.out"
printf "Concurrent client outputs: %s, %s\n" "$OUT_CLIENT/dual-c1.out" "$OUT_CLIENT/dual-c2.out"
