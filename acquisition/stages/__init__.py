"""Pipeline stages.

One package per stage. Each exposes its request, result, and stage class through its
`__init__.py`; everything else is an implementation detail.

Stages may import `core`, `utils`, and `contracts`. A stage must never import another
stage.
"""
