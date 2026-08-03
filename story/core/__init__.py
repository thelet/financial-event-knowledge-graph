"""Shared meaning for the story layer: the frozen types, the id scheme, the manifest.

`core/` carries application meaning that more than one stage needs; it never imports a stage,
never opens a socket and never reads a clock — with the single, named exception of
`manifest.py`, which owns `created_at` because a manifest is the only artifact §14 permits to
carry one.
"""

from __future__ import annotations
