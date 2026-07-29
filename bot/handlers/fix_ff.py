"""Deprecated formatting helper.

Formatting source files during module import is unsafe and used to cause
unrelated files to be rewritten whenever this module was imported.  Keep the
module importable for backwards compatibility, but intentionally do nothing.
"""
