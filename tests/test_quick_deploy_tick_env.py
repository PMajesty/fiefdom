"""Синхронизация TICK_* в remote .env при quick_deploy."""
from __future__ import annotations

import io

from deploy.quick_deploy import TICK_ENV, sync_remote_tick_env


class _FakeSFTP:
    def __init__(self, initial: str):
        self.path = "/opt/fiefdom/.env"
        self.data = initial.encode("utf-8")

    def open(self, path: str, mode: str = "r"):
        assert path == self.path
        if mode == "r":
            buf = io.BytesIO(self.data)
            buf.__enter__ = lambda: buf  # type: ignore[method-assign]
            buf.__exit__ = lambda *a: False  # type: ignore[method-assign]
            return buf

        outer = self

        class _Writer(io.BytesIO):
            def write(self_inner, data):  # noqa: N805
                if isinstance(data, str):
                    data = data.encode("utf-8")
                return super().write(data)

            def close(self_inner):  # noqa: N805
                outer.data = self_inner.getvalue()
                return super().close()

            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *args):
                self_inner.close()
                return False

        return _Writer()


def test_sync_remote_tick_env_updates_and_appends():
    initial = (
        "TELEGRAM_BOT_TOKEN=x\n"
        "TICK_HOUR=13\n"
        "TICK_MINUTE=0\n"
        "TICK_HOUR_2=19\n"
        "TICK_MINUTE_2=0\n"
        "ADMIN_USER_ID=1\n"
    )
    sftp = _FakeSFTP(initial)
    sync_remote_tick_env(sftp, "/opt/fiefdom")
    text = sftp.data.decode("utf-8")
    for key, value in TICK_ENV.items():
        assert f"{key}={value}" in text
    assert text.count("TICK_HOUR=") == 1
    assert "TICK_HOUR=13\n" not in text
    assert "TICK_HOUR_2=19" not in text
    assert "TELEGRAM_BOT_TOKEN=x" in text
