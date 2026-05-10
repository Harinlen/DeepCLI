"""Format conversion helpers — universal types → SDK-native request formats.

Sub-modules
-----------
- ``anthropic`` — universal → Anthropic Messages API format
- ``openai``    — universal → OpenAI Chat Completions API format

All functions are pure (no I/O, no SDK state) and easy to unit-test.
"""
