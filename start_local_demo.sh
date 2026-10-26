#!/usr/bin/env bash
# Preventra - run the GLP-1 demo on this Mac, as a backup to Railway.
#
#   ./start_local_demo.sh          start everything (first run installs what's missing)
#   ./start_local_demo.sh stop     stop everything
#   ./start_local_demo.sh status   is each part up?
#
# Starts four things, all on this computer:
#   Readmissions API  :8001   the sign-in service (it issues the login for both apps)
#   GLP-1 API         :8000
#   GLP-1 app         :5173
#   Portal            :5175   the sign-in page - open http://localhost:5175
#
# It uses the SAME database as the live app (the MONGODB_URI and
# SHARED_SECRET_KEY in GLP1/Backend/.env), so the same logins and patients
# work. Nothing is written to any file: the settings are passed to the
# programs as they start. Logs go to .local-demo/ in the project folder.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOGS="$ROOT/.local-demo"
mkdir -p "$LOGS"
PORTS=(8001 8000 5173 5175)
NAMES=("Readmissions API (sign-in)" "GLP-1 API" "GLP-1 app" "Portal")

pid_on() { lsof -ti tcp:"$1" -sTCP:LISTEN 2>/dev/null | head -1; }

status() {
  for i in "${!PORTS[@]}"; do
    if [ -n "$(pid_on "${PORTS[$i]}")" ]; then echo "  up    ${NAMES[$i]}  (port ${PORTS[$i]})"
    else echo "  DOWN  ${NAMES[$i]}  (port ${PORTS[$i]})"; fi
  done
}

stop_all() {
  for p in "${PORTS[@]}"; do
    for pid in $(lsof -ti tcp:"$p" -sTCP:LISTEN 2>/dev/null); do kill "$pid" 2>/dev/null || true; done
  done
  echo "Stopped."
}

case "${1:-start}" in
  stop)   stop_all; exit 0 ;;
  status) status; exit 0 ;;
  start)  ;;
  *) echo "Use: $0 [start|stop|status]"; exit 1 ;;
esac

# ---- the two settings, read from GLP-1's .env (never printed)
ENV_FILE="$ROOT/GLP1/Backend/.env"
[ -f "$ENV_FILE" ] || { echo "Missing $ENV_FILE - it holds the database address and the secret."; exit 1; }
read_env() {
  python3 - "$ENV_FILE" "$1" <<'PY'
import sys
path, key = sys.argv[1], sys.argv[2]
for line in open(path):
    line = line.strip()
    if line.startswith(key + "="):
        v = line[len(key) + 1:].strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
            v = v[1:-1]
        print(v)
        break
PY
}
DB_URI="$(read_env MONGODB_URI)"
SECRET="$(read_env SHARED_SECRET_KEY)"
[ -n "$DB_URI" ] || { echo "MONGODB_URI is empty in GLP1/Backend/.env"; exit 1; }
[ -n "$SECRET" ] || { echo "SHARED_SECRET_KEY is empty in GLP1/Backend/.env"; exit 1; }

# ---- anything already on these ports?
for i in "${!PORTS[@]}"; do
  if [ -n "$(pid_on "${PORTS[$i]}")" ]; then
    echo "Port ${PORTS[$i]} is already in use (${NAMES[$i]}). Run: $0 stop   then try again."; exit 1
  fi
done

# ---- one-time installs
if [ ! -x "$ROOT/GLP1/Backend/.venv/bin/uvicorn" ]; then
  echo "Installing the GLP-1 API (first time only)..."
  python3 -m venv "$ROOT/GLP1/Backend/.venv"
  "$ROOT/GLP1/Backend/.venv/bin/pip" install -q -r "$ROOT/GLP1/Backend/requirements.txt"
fi
if [ ! -x "$ROOT/Readmissions/.venv/bin/uvicorn" ]; then
  echo "Installing the sign-in service (first time only)..."
  [ -d "$ROOT/Readmissions/.venv" ] || python3 -m venv "$ROOT/Readmissions/.venv"
  "$ROOT/Readmissions/.venv/bin/pip" install -q -r "$ROOT/Readmissions/requirements-api.txt"
fi
if [ ! -d "$ROOT/GLP1/Frontend/node_modules" ]; then
  echo "Installing the GLP-1 app (first time only)..."
  npm --prefix "$ROOT/GLP1/Frontend" install --silent
fi

LOCAL_ORIGINS="http://localhost:5173,http://localhost:5174,http://localhost:5175"

echo "Starting..."
# 1. Sign-in service (Readmissions API)
( cd "$ROOT/Readmissions" && \
  MONGO_URI="$DB_URI" MONGO_DB=neuroshield SHARED_SECRET_KEY="$SECRET" API_KEY="" \
  ALLOWED_ORIGINS="$LOCAL_ORIGINS" SHARED_AUTH_DEFAULT_APPS=glp1,readmissions \
  nohup .venv/bin/uvicorn api.main:app --port 8001 > "$LOGS/readmissions-api.log" 2>&1 & )

# 2. GLP-1 API (settings passed here win over its .env)
( cd "$ROOT/GLP1/Backend" && \
  CORS_ORIGINS='["http://localhost:5173","http://localhost:5175"]' \
  nohup .venv/bin/uvicorn main:app --port 8000 > "$LOGS/glp1-api.log" 2>&1 & )

# 3. GLP-1 app, pointed at the local APIs and Portal
( cd "$ROOT/GLP1/Frontend" && \
  VITE_API_URL=http://localhost:8000 VITE_AUTH_URL=http://localhost:8001 \
  VITE_PORTAL_URL=http://localhost:5175 VITE_READMISSIONS_URL=http://localhost:5174 \
  nohup npx vite --port 5173 --strictPort > "$LOGS/glp1-app.log" 2>&1 & )

# 4. Portal (its committed config.js already points at localhost)
( cd "$ROOT" && nohup npx --yes serve Portal -l 5175 > "$LOGS/portal.log" 2>&1 & )

# ---- wait until each answers (the APIs can take ~30 s to reach the database)
echo "Waiting for everything to come up (up to 90 seconds)..."
for _ in $(seq 1 45); do
  up=0; for p in "${PORTS[@]}"; do [ -n "$(pid_on "$p")" ] && up=$((up + 1)); done
  [ "$up" -eq "${#PORTS[@]}" ] && break
  sleep 2
done
status
if curl -s http://localhost:8001/auth/config | grep -q '"secret_configured": *true'; then
  echo "  sign-in service has its secret: OK"
else
  echo "  sign-in service is not ready yet - see .local-demo/readmissions-api.log"
fi
echo
echo "Open http://localhost:5175 and sign in with the usual demo logins."
echo "To stop:  $0 stop"
