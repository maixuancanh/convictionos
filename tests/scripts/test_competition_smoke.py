import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts" / "competition_smoke.py"
SPEC = importlib.util.spec_from_file_location("competition_smoke", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


async def test_authorized_phase_requires_explicit_flag_and_existing_intent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        MODULE,
        "parse_args",
        lambda: type(
            "Args",
            (),
            {
                "phase": "authorized-paper",
                "authorize_paper_order": False,
                "intent_id": None,
                "base_url": "http://127.0.0.1:8000",
                "output": None,
            },
        )(),
    )
    with pytest.raises(SystemExit, match="--authorize-paper-order"):
        await MODULE.main()


async def test_authorized_phase_requires_intent_after_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        MODULE,
        "parse_args",
        lambda: type(
            "Args",
            (),
            {
                "phase": "authorized-paper",
                "authorize_paper_order": True,
                "intent_id": None,
                "base_url": "http://127.0.0.1:8000",
                "output": None,
            },
        )(),
    )
    with pytest.raises(SystemExit, match="--intent-id"):
        await MODULE.main()
