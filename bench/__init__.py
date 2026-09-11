"""Reproducible performance benchmarks.

The suite exists because the project's performance targets are stated as
non-negotiable, and a target that is not measured on every change erodes quietly
(ADR-0015). Every case runs against local resources or deterministic fake backends, so
results do not depend on a GPU, a network or an API key, and CI can gate on them.
"""
