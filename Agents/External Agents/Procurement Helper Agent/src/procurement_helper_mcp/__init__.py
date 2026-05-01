"""Procurement Helper MCP -- deterministic Python tools for the
Procurement Article Research workflow.

This package exists to remove LLM hallucinations from steps that are
inherently deterministic (string parsing, array slicing, JSON merging,
template rendering, S3 existence checks). The accompanying SAM agent
("ProcurementHelperAgent") routes workflow node calls to the right
tool; the heavy lifting is pure Python.
"""

__version__ = "1.0.0"
