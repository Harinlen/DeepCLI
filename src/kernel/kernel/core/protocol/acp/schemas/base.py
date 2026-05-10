"""Base model for all ACP wire-format schemas.

All ACP method names and field names use camelCase on the wire.
``AcpModel`` configures Pydantic to:

* Accept camelCase field names during ``model_validate`` (aliases from
  ``alias_generator``).
* Also accept snake_case names (``populate_by_name=True``) so Python
  code can construct models without quoting every key.
* Emit camelCase names in ``model_dump_json(by_alias=True)`` so the
  codec produces spec-compliant JSON.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class AcpModel(BaseModel):
    """Base class for every ACP wire-format Pydantic model."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )
