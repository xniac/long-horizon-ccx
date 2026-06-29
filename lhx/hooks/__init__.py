"""Claude Code hook entry points for the long-horizon module.

Each module here is runnable as ``python -m lhx.hooks.<name>``. They read the
hook event JSON from stdin and emit a JSON decision on stdout, following the
Claude Code hooks contract. They are intentionally tiny — all logic lives in the
``lhx`` primitives — so they are easy to audit and to re-simplify as the model
improves.
"""
