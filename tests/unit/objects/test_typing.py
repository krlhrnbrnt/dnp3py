"""Static types that callers of the object classes rely on under mypy --strict."""

from pathlib import Path

from mypy import api

from dnp3.objects.registry import get_registry_copy


def test_registered_classes_type_size_as_int(tmp_path: Path) -> None:
    """SIZE is int, not int | None, on every registered class, so callers can do arithmetic with it."""
    classes = sorted(get_registry_copy().values(), key=lambda c: (c.GROUP, c.VARIATION))
    snippet = tmp_path / "size_types.py"
    snippet.write_text(
        "\n".join(
            [
                "from typing import assert_type",
                *(f"from {cls.__module__} import {cls.__name__}" for cls in classes),
                *(f"assert_type({cls.__name__}.SIZE, int)" for cls in classes),
            ]
        )
        + "\n"
    )
    stdout, stderr, status = api.run(["--strict", str(snippet)])
    assert status == 0, stdout + stderr
