"""Fake backend servers used by contract tests and benchmarks.

Each fake replays the real byte-level streaming format of the backend it stands in
for — Ollama's newline-delimited JSON, llama.cpp's SSE frames with a final `timings`
object — so a contract test exercises the adapter's actual parsing, not an
idealisation of it. No test in this project needs a real backend or a network call.
"""
