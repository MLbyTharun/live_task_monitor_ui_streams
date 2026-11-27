from __future__ import annotations

from datetime import UTC, datetime

from ltm.prompts import PromptLibrary
from ltm.schemas import Snapshot, Task


def test_prompt_rendering_keeps_json_braces(tmp_path):
    system = tmp_path / "system.md"
    verify = tmp_path / "verify.md"
    system.write_text("You are a watcher.", encoding="utf-8")
    verify.write_text(
        "Task: {{task_title}}\nCameras:\n{{stream_list}}\n"
        'Return {"task_completed": true, "confidence": 0.0}\n',
        encoding="utf-8",
    )

    prompts = PromptLibrary(system, verify)
    task = Task(id="1", title="Place block", description="desc", order=1)
    frame = Snapshot(
        stream_id="cam1",
        stream_name="Camera One",
        captured_at=datetime.now(UTC),
        seq=1,
        jpeg=b"x",
        width=10,
        height=10,
    )

    system_text, user_text = prompts.build(task, [frame])

    assert system_text == "You are a watcher."
    assert "Place block" in user_text
    assert "Camera One (cam1)" in user_text
    # Literal JSON braces must survive template rendering.
    assert '{"task_completed": true, "confidence": 0.0}' in user_text
