"""ACP wire-format Pydantic models.

These are a faithful hand-written transcription of the types defined in
``references/acp/protocol/schema.md`` (the local ACP spec mirror).
They are used exclusively by the ACP codec and dispatcher; nothing
outside ``kernel/protocol/acp/`` should import from here.
"""
