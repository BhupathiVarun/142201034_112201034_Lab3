#!/usr/bin/env bash
# Run end-to-end tests for the UDP client/server project
# Usage: ./run_all.sh [port]
set -euo pipefail
# ...existing code...

PORT="${1:-1234}"
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

# Ensure launchers are executable
chmod +x server client second.sh 2>/dev/null || true

# Output directories (Partner B)
OUT_CLIENT="B/Client_Non_Thread/outputs"
OUT_SERVER="B/Server_Thread/outputs"
mkdir -p "$OUT_CLIENT" "$OUT_SERVER"

# Resolve input file path
FILE="Dostoyevsky.txt"
[[ -f "$FILE" ]] || FILE="B/Dostoyevsky.txt"
if [[ ! -f "$FILE" ]]; then
  echo "Input text file not found: Dostoyevsky.txt or B/Dostoyevsky.txt"
  exit 1
fi

# Clean previous outputs and free port
rm -f \
  "$OUT_SERVER/server.out" \
  "$OUT_CLIENT/typed.out" \
  "$OUT_CLIENT/file.out" \
  "$OUT_CLIENT/dual-c1.out" \
  "$OUT_CLIENT/dual-c2.out" || true

(fuser -k "${PORT}"/udp || true) >/dev/null 2>&1 || true

# Start server in background
./server "$PORT" > "$OUT_SERVER/server.out" 2>&1 &
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
    printf "%s" "$text" | script -q -c "./client 127.0.0.1 $PORT" /dev/null >"$out" 2>&1 || true
  else
    printf "%s" "$text" | ./client 127.0.0.1 "$PORT" >"$out" 2>&1 || true
  fi
}

# 1) Typed test
run_typed $'five\ntwo\nthree\nq\n' "$OUT_CLIENT/typed.out"

# 2) Single file redirect test
./client 127.0.0.1 "$PORT" < "$FILE" > "$OUT_CLIENT/file.out" 2>&1 || true

# 3) Concurrent clients test
./client 127.0.0.1 "$PORT" < "$FILE" > "$OUT_CLIENT/dual-c1.out" 2>&1 &
c1=$!
./client 127.0.0.1 "$PORT" < "$FILE" > "$OUT_CLIENT/dual-c2.out" 2>&1 &
c2=$!
wait "$c1" "$c2" || true

# Give server a moment to flush output
sleep 0.2

# Summary
printf "\n=== Test Summary ===\n"
printf "Server log: %s\n" "$OUT_SERVER/server.out"
printf "Typed client output: %s\n" "$OUT_CLIENT/typed.out"
printf "Single-file client output: %s\n" "$OUT_CLIENT/file.out"
printf "Concurrent client outputs: %s, %s\n" "$OUT_CLIENT/dual-c1.out" "$OUT_CLIENT/dual-c2.out"