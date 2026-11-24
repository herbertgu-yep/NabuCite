#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

for tool in docker python3.12 node pnpm; do
  command -v "$tool" >/dev/null || { echo "启动需要 $tool，请先安装。" >&2; exit 1; }
done
docker compose version >/dev/null || { echo '请安装 Docker Compose。' >&2; exit 1; }
docker info >/dev/null 2>&1 || { echo '无法连接 Docker。请先启动 Docker Desktop，并确认当前终端有访问权限。' >&2; exit 1; }

(
  cd backend
  if [[ ! -x .venv/bin/python ]]; then
    python_bin=$(python3.12 -c 'import os, sys; print(os.path.realpath(sys.executable))')
    "$python_bin" -m venv .venv
  fi
  .venv/bin/python -m pip install --disable-pip-version-check --quiet -r requirements.txt
  if [[ ! -e .env ]]; then (umask 077; cp -n .env.example .env); fi
  .venv/bin/python - <<'PY'
import os
import secrets
import sys
from getpass import getpass
from dotenv import dotenv_values, set_key

settings = dotenv_values(".env")
for name in ("POSTGRES_PASSWORD", "JWT_SECRET", "S3_PASSWORD", "SEARCH_PASSWORD", "RABBIT_PASSWORD"):
    if not (os.environ.get(name) or settings.get(name)):
        set_key(".env", name, secrets.token_urlsafe(48))
if not (os.environ.get("MODEL_API_KEY") or settings.get("MODEL_API_KEY")):
    if not sys.stderr.isatty():
        sys.exit("请先在 backend/.env 中填写 MODEL_API_KEY，再运行 ./start.sh。")
    key = getpass("首次启动，请输入模型 API Key（输入不回显）：").strip()
    if not key:
        sys.exit("未填写模型 API Key。准备好后可重新运行 ./start.sh。")
    set_key(".env", "MODEL_API_KEY", key)
PY
)
(
  cd frontend
  if [[ ! -x node_modules/.bin/vite ]] || ! cmp -s pnpm-lock.yaml node_modules/.pnpm/lock.yaml; then
    pnpm install --frozen-lockfile
  fi
)

docker compose --env-file backend/.env up -d --build --wait db object-store broker search
(
  cd backend
  .venv/bin/python -m alembic upgrade head
  .venv/bin/python seed.py --if-empty
)

strategy=$(cd backend && .venv/bin/python -c 'import config, os; print(os.getenv("RETRIEVAL_STRATEGY","bm25"))')
if [[ "$strategy" != bm25 ]]; then
  docker compose --env-file backend/.env --profile vector up -d --wait milvus
fi
docker compose --env-file backend/.env up -d --build ingest-worker

api_pid=''
web_pid=''
cleanup() {
  for pid in "$web_pid" "$api_pid"; do
    if [[ -n "$pid" ]]; then kill "$pid" 2>/dev/null || true; fi
  done
  wait || true
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

ports=$(python3.12 - <<'PY'
import errno
import socket


def available_port(preferred):
    with socket.socket() as probe:
        for port in range(preferred, preferred + 100):
            try:
                probe.bind(("127.0.0.1", port))
                return port
            except OSError as exc:
                if exc.errno != errno.EADDRINUSE:
                    raise SystemExit(f"无法检查本地端口 {port}：{exc}") from exc
    raise SystemExit(f"本地端口 {preferred} 至 {preferred + 99} 均已占用。")


print(available_port(8000), available_port(5173))
PY
)
read -r api_port web_port <<< "$ports"
mkdir -p infra/monitoring/targets
printf '[{"targets":["host.docker.internal:%s"]}]\n' "$api_port" > infra/monitoring/targets/api.json
echo "NabuCite 页面：http://127.0.0.1:$web_port"
(cd backend && export NABUCITE_WEB_PORT="$web_port" && exec .venv/bin/python -m uvicorn main:app --reload --reload-dir . --no-access-log --host 127.0.0.1 --port "$api_port") &
api_pid=$!
(cd frontend && export NABUCITE_API_TARGET="http://127.0.0.1:$api_port" && exec ./node_modules/.bin/vite --host 127.0.0.1 --port "$web_port" --strictPort) &
web_pid=$!
while kill -0 "$api_pid" 2>/dev/null && kill -0 "$web_pid" 2>/dev/null; do sleep 1; done
if ! kill -0 "$api_pid" 2>/dev/null; then wait "$api_pid"; else wait "$web_pid"; fi
