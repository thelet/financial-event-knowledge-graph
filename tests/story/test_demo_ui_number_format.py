"""`app.js:formatNumber` against `api.py:_display_number`, in both languages at once.

**This is the one claim about `app.js` this suite can prove by execution rather than by reading**,
and it is here because the adversarial review of 2026-08-05 measured the two formatters
disagreeing. The panel had `Number.parseFloat(value.toPrecision(10))` and the server has
`f"{value:.10g}"`; they agree on the corpus, whose largest observation value is 6.79e8, and they
disagree twice off it:

| value | old `app.js` | `api.py:_display_number` |
| --- | --- | --- |
| `1234567890.5` | `1234567891` | `1234567890` |
| `1234567890123` | `1234567890123` | `1.23456789e+12` |

The first is a rounding rule — C's `%g` sends a tie to the even digit and JavaScript's rounding
sends it away from zero — and the second is C's switch to exponential form once the exponent
reaches the precision. Neither could lie about a value in this corpus today. Both could lie about
the same fact in two panels, which is what the facts panel and the sources panel are.

So `app.js` now implements `%.10g` itself, and this file is what makes that a measurement:
`formatNumber` is run under `node` over a table of values and its output is compared to Python's
own `%` operator, character for character. Where there is no JavaScript engine on the path the
file **skips rather than pretending** — the same rule `test_demo_ui_app.py` applies to
`node --check`, and for the same reason.
"""

from __future__ import annotations

import json
import random
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from story.demo_ui import api

REPO_ROOT = Path(__file__).resolve().parents[2]
APP = REPO_ROOT / "story" / "demo_ui" / "static" / "app.js"

#: The two markers `app.js` carries so this file can lift the formatter out without a bundler, a
#: parser or a `document`. Importing `app.js` itself is not an option: it looks up sixty elements
#: and builds a canvas view at module scope.
BEGIN = "/* BEGIN NUMBER FORMAT"
END = "/* END NUMBER FORMAT */"


def format_block() -> str:
    source = APP.read_text(encoding="utf-8")
    start = source.find(BEGIN)
    finish = source.find(END)
    assert start >= 0, f"app.js no longer carries {BEGIN!r}"
    assert finish > start, f"app.js no longer carries {END!r} after {BEGIN!r}"
    return source[start:finish]


def sample_values() -> list[float]:
    """The two measured divergences, the corpus's own numbers, and a seeded sweep.

    Seeded because a formatter test that passed on Tuesday and failed on Wednesday over a value
    nobody recorded is not a test, it is a rumour.
    """
    generator = random.Random(20260805)
    values: list[float] = [
        0.0, -0.0, 1.0, -1.0,
        # The residue §7 names, and the two the review measured.
        15.899999999999999, 1234567890.5, 1234567890123.0,
        # Real values from the demo corpus and the ranking panel.
        -12.6, 3.3, 0.778501289, 679000000.0, 28837.0, 35600.0,
        # The edges of `%g`'s own two decisions: the exponent switch and the carry.
        1e-5, 1e-4, 0.0001, 9.999999999e9, 9999999999.5, 1e10, 1e21, -1e21,
        0.30000000000000004, 2.5e-10, 1 / 3, 2 / 3,
        5e-324, 1.7976931348623157e308, -1.7976931348623157e308,
    ]
    for _ in range(500):
        values.append(generator.uniform(-1e6, 1e6))
        values.append(generator.uniform(-1.0, 1.0))
        values.append(generator.uniform(-1e12, 1e12))
        values.append(float(generator.randint(-10 ** 13, 10 ** 13)))
        values.append(generator.uniform(-1e-6, 1e-6))
        values.append(generator.uniform(-10, 10) * (10.0 ** generator.randint(-300, 300)))
    return values


HARNESS = """
import { formatNumber } from './block.mjs';
import { readFileSync } from 'node:fs';
const values = JSON.parse(readFileSync(process.argv[2], 'utf8'));
process.stdout.write(JSON.stringify(values.map((value) => formatNumber(value))));
"""


def test_the_two_formatters_agree_on_every_value_in_the_table(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("no JavaScript engine on the path; the two formatters are unchecked here")

    (tmp_path / "block.mjs").write_text(format_block(), encoding="utf-8")
    (tmp_path / "harness.mjs").write_text(HARNESS, encoding="utf-8")
    values = sample_values()
    (tmp_path / "values.json").write_text(json.dumps(values), encoding="utf-8")

    finished = subprocess.run(  # noqa: S603 - a fixed argv, no shell, a path from `which`
        [node, str(tmp_path / "harness.mjs"), str(tmp_path / "values.json")],
        capture_output=True, text=True, timeout=120, cwd=tmp_path)
    assert finished.returncode == 0, finished.stderr
    rendered = json.loads(finished.stdout)

    expected = [api._display_number(value) for value in values]  # noqa: SLF001 - the thing tested
    disagreements = [
        (value, theirs, ours)
        for value, ours, theirs in zip(values, expected, rendered, strict=True)
        if ours != theirs
    ]
    assert disagreements == [], (
        f"{len(disagreements)} of {len(values)} values render differently in the two languages; "
        f"first five: {disagreements[:5]}")


def test_the_two_values_the_review_measured_are_in_the_table_and_still_the_hard_ones() -> None:
    """A guard on the guard: if these two ever leave `sample_values`, the test above keeps
    passing while proving nothing about the case that was actually wrong."""
    values = sample_values()
    assert 1234567890.5 in values
    assert 1234567890123.0 in values
    assert api._display_number(1234567890.5) == "1234567890"  # noqa: SLF001
    assert api._display_number(1234567890123.0) == "1.23456789e+12"  # noqa: SLF001


def test_the_extracted_block_is_the_formatter_and_not_an_empty_slice() -> None:
    """`format_block` slices on two comments. A rename that made it return nothing would let the
    comparison above pass over an empty module and report success."""
    block = format_block()
    assert "export function formatNumber" in block
    assert "roundHalfEven" in block
    assert "toExponential(20)" in block, "the block no longer reads the value's own digits"
    for forbidden in ("document", "state.", "dom."):
        assert forbidden not in block, (
            f"the number-format block touches {forbidden!r}; it is extracted and run on its own "
            f"and must stay self-contained")


def test_the_server_still_formats_with_the_precision_the_block_implements() -> None:
    """The other end of the wire. `_display_number` moving to `%.12g` would make the panel and
    the artifact two renderings of one number again, silently."""
    source = (REPO_ROOT / "story" / "demo_ui" / "api.py").read_text(encoding="utf-8")
    assert re.search(r'return f"\{value:\.10g\}"', source), (
        "api.py:_display_number is no longer %.10g; app.js implements exactly that")
