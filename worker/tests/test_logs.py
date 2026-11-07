"""Logs are JSON and never contain the token, lyrics or style (PRD sections 8 and 11)."""

import json
import logging

import pytest
from worker_support import TOKEN, Worker, song_params

from studio_worker.logs import JsonFormatter


async def test_no_secrets_or_lyrics_in_logs(
    worker: Worker, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    params = song_params(style="STYLE-MARKER", lyrics="[Verse]\nLYRICS-MARKER")
    job = (await worker.submit(params)).json()
    await worker.wait_for(job["id"], "succeeded")
    await worker.submit({"style": "STYLE-MARKER" * 200, "lyrics": "LYRICS-MARKER"})  # a 422
    await worker.client.get("/v1/status", headers={"Authorization": "Bearer wrong"})
    formatter = JsonFormatter()
    lines = [formatter.format(record) for record in caplog.records]
    assert any(json.loads(line).get("job_id") == job["id"] for line in lines)
    text = "\n".join(lines)
    for secret in (TOKEN, "STYLE-MARKER", "LYRICS-MARKER"):
        assert secret not in text


def test_formatter_emits_one_json_object_with_extras() -> None:
    record = logging.makeLogRecord(
        {"name": "x", "levelname": "INFO", "msg": "job %s", "args": ("done",), "job_id": "j1"}
    )
    data = json.loads(JsonFormatter().format(record))
    assert data["msg"] == "job done" and data["job_id"] == "j1" and data["level"] == "INFO"
