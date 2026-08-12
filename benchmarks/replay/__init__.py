"""Replayable retrieval-quality harness for jdocmunch-mcp.

Locks retrieval behavior at release boundaries. Every future release runs
fixtures through search_sections and computes nDCG@k / MRR / Recall@k against
the saved baseline. CI fails when any aggregate metric drops by more than
the configured gate (default 2%).

See run_replay.py for CLI usage and fixtures/ for fixture format.
"""
