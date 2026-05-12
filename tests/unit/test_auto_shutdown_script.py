"""The bundled auto_shutdown.sh stops the pod, not destroys it."""

import importlib.resources


def _script() -> str:
    ref = importlib.resources.files("rp.assets").joinpath("auto_shutdown.sh")
    with importlib.resources.as_file(ref) as p:
        return p.read_text()


def test_auto_shutdown_calls_stop_endpoint():
    s = _script()
    assert "/v1/pods/${RUNPOD_POD_ID}/stop" in s


def test_auto_shutdown_uses_post_not_delete():
    s = _script()
    assert "-X POST" in s
    assert "-X DELETE" not in s


def test_auto_shutdown_log_message_says_stop():
    s = _script()
    assert "Stopping pod" in s
    assert "Destroying pod" not in s
