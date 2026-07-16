#!/usr/bin/env python3
"""Упаковка src/ + requirements + tests, загрузка на VPS, restart systemd."""
from __future__ import annotations

import io
import os
import sys
import tarfile
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import paramiko
from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
load_dotenv(SCRIPT_DIR / "secrets.env")
load_dotenv(PROJECT_ROOT / ".env")

VPS_HOST = os.getenv("VPS_HOST", "151.241.155.79")
VPS_USER = os.getenv("VPS_USER", "root")
VPS_PASS = os.getenv("VPS_PASS", "")
REMOTE_DIR = os.getenv("REMOTE_DIR", "/opt/fiefdom")
BOT_USER = "botuser"
SERVICE = "fiefdom"

SKIP_DIRS = {"__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", ".git", ".venv", "venv"}
SKIP_EXT = {".pyc", ".pyo"}
SSH_RETRIES = 4
SSH_RETRY_DELAY = 5

# Плановые слоты: quick_deploy обязан синхронизировать .env, иначе старые
# TICK_HOUR/TICK_HOUR_2 смешаются с дефолтами _3/_4.
TICK_ENV = {
    "TICK_HOUR": "10",
    "TICK_MINUTE": "0",
    "TICK_HOUR_2": "13",
    "TICK_MINUTE_2": "0",
    "TICK_HOUR_3": "16",
    "TICK_MINUTE_3": "0",
    "TICK_HOUR_4": "19",
    "TICK_MINUTE_4": "0",
}


def _filter(tarinfo: tarfile.TarInfo):
    parts = tarinfo.name.replace("\\", "/").split("/")
    if any(p in SKIP_DIRS for p in parts):
        return None
    if os.path.splitext(tarinfo.name)[1].lower() in SKIP_EXT:
        return None
    return tarinfo


def pack_source() -> bytes:
    print(">>> Packing source…")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(str(PROJECT_ROOT / "src"), arcname="src", filter=_filter)
        tar.add(str(PROJECT_ROOT / "requirements.txt"), arcname="requirements.txt")
        tests_dir = PROJECT_ROOT / "tests"
        if tests_dir.exists():
            tar.add(str(tests_dir), arcname="tests", filter=_filter)
        pytest_ini = PROJECT_ROOT / "pytest.ini"
        if pytest_ini.exists():
            tar.add(str(pytest_ini), arcname="pytest.ini")
        design = PROJECT_ROOT / "valley_game_design.md"
        if design.exists():
            tar.add(str(design), arcname="valley_game_design.md")
    data = buf.getvalue()
    print(f"    packed {len(data):,} bytes")
    return data


def run(client: paramiko.SSHClient, cmd: str) -> str:
    _, stdout, stderr = client.exec_command(cmd)
    out = stdout.read().decode("utf-8", errors="replace").strip()
    err = stderr.read().decode("utf-8", errors="replace").strip()
    code = stdout.channel.recv_exit_status()
    if out:
        print(f"    {out}")
    if err:
        print(f"    ERR: {err}")
    if code != 0:
        raise RuntimeError(f"command failed ({code}): {cmd}")
    return out


def sync_remote_tick_env(sftp: paramiko.SFTPClient, remote_dir: str) -> None:
    """Обновляет TICK_* в remote .env (upsert), чтобы слоты не смешались со старыми."""
    env_path = f"{remote_dir}/.env"
    try:
        with sftp.open(env_path, "r") as f:
            raw = f.read().decode("utf-8", errors="replace")
    except FileNotFoundError as exc:
        raise RuntimeError(f"remote .env not found: {env_path}") from exc

    lines = raw.splitlines()
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            out.append(line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in TICK_ENV:
            out.append(f"{key}={TICK_ENV[key]}")
            seen.add(key)
        else:
            out.append(line)
    for key, value in TICK_ENV.items():
        if key not in seen:
            out.append(f"{key}={value}")
    text = "\n".join(out)
    if not text.endswith("\n"):
        text += "\n"
    with sftp.open(env_path, "w") as f:
        f.write(text.encode("utf-8"))
    print(
        "    tick env synced: "
        + ", ".join(f"{k}={v}" for k, v in TICK_ENV.items() if k.startswith("TICK_HOUR"))
    )


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
                print(f"    попытка {attempt}/{SSH_RETRIES} не удалась ({exc}), повтор…")
                time.sleep(SSH_RETRY_DELAY)
    raise RuntimeError(f"SSH failed: {last}") from last


def main() -> None:
    if not VPS_PASS:
        raise SystemExit("Задай VPS_PASS в deploy/secrets.env")
    archive = pack_source()
    print(f">>> Connecting to {VPS_USER}@{VPS_HOST}…")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh_connect(client)
    print("    connected")

    print(">>> Uploading…")
    sftp = client.open_sftp()
    with sftp.open("/tmp/fiefdom_update.tar.gz", "wb") as f:
        f.write(archive)
    sftp.close()

    print(">>> Applying…")
    run(
        client,
        f"tar -xzf /tmp/fiefdom_update.tar.gz -C {REMOTE_DIR}/ "
        f"&& rm /tmp/fiefdom_update.tar.gz",
    )
    run(
        client,
        f"mkdir -p {REMOTE_DIR}/logs "
        f"&& chown -R {BOT_USER}:{BOT_USER} {REMOTE_DIR}/src {REMOTE_DIR}/requirements.txt "
        f"{REMOTE_DIR}/tests {REMOTE_DIR}/pytest.ini {REMOTE_DIR}/logs 2>/dev/null; true",
    )
    run(client, f"{REMOTE_DIR}/venv/bin/pip install -r {REMOTE_DIR}/requirements.txt -q")
    print(">>> Syncing tick schedule in .env…")
    sftp = client.open_sftp()
    sync_remote_tick_env(sftp, REMOTE_DIR)
    sftp.close()
    print(">>> Restarting…")
    run(client, f"systemctl restart {SERVICE}")
    print(">>> Status:")
    _, stdout, _ = client.exec_command(
        f"systemctl status {SERVICE} --no-pager -l --lines=30"
    )
    print(stdout.read().decode("utf-8", errors="replace"))
    client.close()
    print(">>> Deploy complete!")


if __name__ == "__main__":
    main()
