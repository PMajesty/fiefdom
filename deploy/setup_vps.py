#!/usr/bin/env python3
"""Первичная установка fiefdom на VPS: Postgres, venv, systemd, .env."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import paramiko
from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).resolve().parent
load_dotenv(SCRIPT_DIR / "secrets.env")
load_dotenv(SCRIPT_DIR.parent / ".env")

VPS_HOST = os.getenv("VPS_HOST", "151.241.155.79")
VPS_USER = os.getenv("VPS_USER", "root")
VPS_PASS = os.getenv("VPS_PASS", "")
REMOTE_DIR = os.getenv("REMOTE_DIR", "/opt/fiefdom")
BOT_USER = "botuser"
SERVICE = "fiefdom"

DB_NAME = os.getenv("DB_NAME", "fiefdom")
DB_USER = os.getenv("DB_USER", "fiefdom_bot")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
POE_API_KEY = os.getenv("POE_API_KEY", "")
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID", "592350620")

SSH_RETRIES = 4
SSH_RETRY_DELAY = 5


def run(client: paramiko.SSHClient, cmd: str, check: bool = True) -> str:
    _, stdout, stderr = client.exec_command(cmd)
    out = stdout.read().decode("utf-8", errors="replace").strip()
    err = stderr.read().decode("utf-8", errors="replace").strip()
    code = stdout.channel.recv_exit_status()
    if out:
        print(f"    {out}")
    if err:
        print(f"    ERR: {err}")
    if check and code != 0:
        raise RuntimeError(f"command failed ({code}): {cmd}")
    return out


def ssh_connect(client: paramiko.SSHClient) -> None:
    last: Exception | None = None
    for attempt in range(1, SSH_RETRIES + 1):
        try:
            client.connect(
                VPS_HOST,
                username=VPS_USER,
                password=VPS_PASS,
                timeout=20,
                banner_timeout=30,
                auth_timeout=30,
            )
            return
        except Exception as exc:
            last = exc
            if attempt < SSH_RETRIES:
                print(f"    попытка {attempt} не удалась ({exc}), повтор…")
                time.sleep(SSH_RETRY_DELAY)
    raise RuntimeError(f"SSH failed: {last}") from last


def main() -> None:
    missing = [
        k
        for k, v in {
            "VPS_PASS": VPS_PASS,
            "DB_PASSWORD": DB_PASSWORD,
            "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
            "POE_API_KEY": POE_API_KEY,
        }.items()
        if not v
    ]
    if missing:
        raise SystemExit("Задай секреты в deploy/secrets.env: " + ", ".join(missing))

    env_content = f"""TELEGRAM_BOT_TOKEN={TELEGRAM_BOT_TOKEN}
POE_API_KEY={POE_API_KEY}
POE_BASE_URL=https://api.poe.com/v1
POE_NARRATIVE_MODEL=Gemini-3.1-Flash-Lite
POE_AGENT_MAX_RETRIES=3
POE_AGENT_RETRY_DELAY=2.0

DB_NAME={DB_NAME}
DB_USER={DB_USER}
DB_PASSWORD={DB_PASSWORD}
DB_HOST=localhost
DB_PORT=5432

TIMEZONE=Europe/Moscow
TICK_HOUR=13
TICK_MINUTE=0
TICK_HOUR_2=19
TICK_MINUTE_2=0
ADMIN_USER_ID={ADMIN_USER_ID}

LOG_DIR={REMOTE_DIR}/logs
"""

    service_unit = f"""[Unit]
Description=Fiefdom (Votchina) Telegram game bot
After=network.target postgresql.service
Wants=postgresql.service

[Service]
Type=simple
User={BOT_USER}
WorkingDirectory={REMOTE_DIR}/src
EnvironmentFile={REMOTE_DIR}/.env
ExecStart={REMOTE_DIR}/venv/bin/python -m app.main
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""

    print(f">>> Connecting to {VPS_USER}@{VPS_HOST}…")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh_connect(client)
    print("    connected")

    print(">>> Directories / user…")
    run(client, f"id -u {BOT_USER} >/dev/null 2>&1 || useradd -r -m -s /bin/bash {BOT_USER}", check=False)
    run(client, f"mkdir -p {REMOTE_DIR}/src {REMOTE_DIR}/logs {REMOTE_DIR}/deploy")
    run(client, f"chown -R {BOT_USER}:{BOT_USER} {REMOTE_DIR}")

    print(">>> Postgres DB…")
    run(
        client,
        f"sudo -u postgres psql -tAc \"SELECT 1 FROM pg_roles WHERE rolname='{DB_USER}'\" | grep -q 1 "
        f"|| sudo -u postgres psql -c \"CREATE USER {DB_USER} WITH PASSWORD '{DB_PASSWORD}';\"",
        check=False,
    )
    run(
        client,
        f"sudo -u postgres psql -tAc \"SELECT 1 FROM pg_database WHERE datname='{DB_NAME}'\" | grep -q 1 "
        f"|| sudo -u postgres psql -c \"CREATE DATABASE {DB_NAME} OWNER {DB_USER};\"",
        check=False,
    )
    run(client, f"sudo -u postgres psql -c \"GRANT ALL PRIVILEGES ON DATABASE {DB_NAME} TO {DB_USER};\"", check=False)
    run(client, f"sudo -u postgres psql -d {DB_NAME} -c \"GRANT ALL ON SCHEMA public TO {DB_USER};\"", check=False)

    print(">>> .env…")
    sftp = client.open_sftp()
    with sftp.open(f"{REMOTE_DIR}/.env", "w") as f:
        f.write(env_content)
    sftp.close()
    run(client, f"chown {BOT_USER}:{BOT_USER} {REMOTE_DIR}/.env && chmod 600 {REMOTE_DIR}/.env")

    print(">>> systemd…")
    run(client, f"cat > /etc/systemd/system/{SERVICE}.service << 'EOF'\n{service_unit}\nEOF")
    run(client, "systemctl daemon-reload")
    run(client, f"systemctl enable {SERVICE}")

    print(">>> Python venv…")
    run(client, f"test -d {REMOTE_DIR}/venv || python3 -m venv {REMOTE_DIR}/venv")
    run(client, f"chown -R {BOT_USER}:{BOT_USER} {REMOTE_DIR}/venv")

    client.close()
    print(">>> Setup base complete. Дальше: python deploy/quick_deploy.py")


if __name__ == "__main__":
    main()
