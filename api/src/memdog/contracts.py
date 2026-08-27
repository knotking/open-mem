"""The content contract.

`ContentRef` is a closed sum type with three cases, and it is what lets an
uploaded PDF and a Drive-fetched one be indistinguishable downstream. The
discriminated union is doing real work here: an item cannot be simultaneously
inline and pending, and `is_downloaded` cannot be set by a caller at all.
"""

from __future__ import annotations

import base64
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, computed_field, field_validator


class Inline(BaseModel):
    kind: Literal["inline"] = "inline"
    text: str | None = None
    bytes_b64: str | None = None
    mime_type: str | None = None  # a hint; the server sniffs and overrides

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_downloaded(self) -> bool:
        return True

    @field_validator("bytes_b64")
    @classmethod
    def _valid_b64(cls, v: str | None) -> str | None:
        if v is not None:
            base64.b64decode(v, validate=True)
        return v

    def model_post_init(self, _ctx: object) -> None:
        if (self.text is None) == (self.bytes_b64 is None):
            raise ValueError("inline content needs exactly one of text or bytes_b64")


class Stored(BaseModel):
    kind: Literal["stored"] = "stored"
    storage_ref: str
    mime_type: str | None = None
    size: int | None = None
    checksum: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_downloaded(self) -> bool:
        return True


class Pending(BaseModel):
    """Only the fetch worker ever sees this.

    It is also what makes an external crawler first-class: it can write a
    reference to a file it discovered through the public API, rather than
    needing an internal queue it cannot reach.
    """

    kind: Literal["pending"] = "pending"
    provider: str
    resource_id: str
    connection_id: str | None = None
    hints: dict = Field(default_factory=dict)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_downloaded(self) -> bool:
        return False


ContentRef = Annotated[Inline | Stored | Pending, Field(discriminator="kind")]


class MemoryRef(BaseModel):
    key: str | None = None
    type: str | None = None


class CaseRef(BaseModel):
    external_id: str
    case_type: str


class ItemAccess(BaseModel):
    """A caller may narrow visibility, never silently widen the default.

    Requesting a level the producer's connection scope does not permit is a
    rejected item, not a downgraded one.
    """

    level: str | None = None
    principals: list[str] = Field(default_factory=list)


class WriteItem(BaseModel):
    external_id: str
    content: ContentRef
    source_type: str | None = None
    data_type: str | None = None
    event_time: datetime | None = None
    tags: list[str] = Field(default_factory=list)
    identifiers: list[str] = Field(default_factory=list)
    access: ItemAccess | None = None
    memory: MemoryRef | None = None
    case: CaseRef | None = None
    metadata: dict = Field(default_factory=dict)


class WriteOptions(BaseModel):
    enrich: bool = True
    priority: Literal["live", "batch"] = "live"


class WriteRequest(BaseModel):
    producer_id: str
    items: list[WriteItem]
    options: WriteOptions = Field(default_factory=WriteOptions)


class WriteResult(BaseModel):
    index: int
    status: Literal["created", "updated", "failed", "dropped"]
    data_id: str | None = None
    state: str | None = None
    error: str | None = None


class WriteResponse(BaseModel):
    accepted: int
    failed: int
    results: list[WriteResult]


class RetrieveFilter(BaseModel):
    project_id: str
    tags: list[str] = Field(default_factory=list)
    since: datetime | None = None
    until: datetime | None = None


class RetrieveRequest(BaseModel):
    """The five named modes are presets over these axes, not an interface."""

    query: str
    filter: RetrieveFilter
    match: list[Literal["vector", "lexical"]] = Field(default_factory=lambda: ["vector", "lexical"])
    rank: Literal["rrf", "none"] = "rrf"
    limit: int = Field(default=20, ge=1, le=200)


class Citation(BaseModel):
    data_id: str
    chunk_id: str
    text: str
    span_start: int
    span_end: int
    score: float
    matched_by: list[str]
    state: str


class RetrieveResponse(BaseModel):
    query_id: str
    results: list[Citation]
    model_id: str
