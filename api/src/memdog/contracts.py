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


class EnrichmentOptions(BaseModel):
    """What AI enrichment should do, when it is asked for at all.

    Separated from the write because these are the expensive parts: embedding
    costs a model call per chunk, summarisation costs one per item, and both
    are useless on data nobody will query.
    """

    embed: bool = True          # vectors, so retrieval can find it
    summarize: bool = True      # the envelope: title, summary, keywords
    # Overrides for this request only. Neither is persisted as configuration --
    # a per-request override that silently became the default would be a way to
    # change a project's behaviour without an audit trail on the setting.
    prompt: str | None = None
    model_id: str | None = None


class WriteOptions(BaseModel):
    # `None` means "whatever the project decided", which is what the
    # `enrich_by_default` setting exists to say -- and which nothing consulted,
    # so a project that turned it on got silence. An explicit `true` or `false`
    # still wins, because a per-request option is the most specific level of the
    # settings chain.
    #
    # The resolved default is OFF. Recording data is cheap and synchronous;
    # anything that spends money is opt-in, and a default that quietly bills
    # people is the wrong default however convenient it looks in a demo.
    enrich: bool | None = None
    enrichment: EnrichmentOptions = Field(default_factory=EnrichmentOptions)
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
    # Derived, never assignable. An item awaiting a fetch is `stored` with
    # is_downloaded false -- that is not a fourth rung on the staircase.
    is_downloaded: bool | None = None
    # Reported because the caller should not have to query to discover where
    # their own write went -- including memberships a routing rule or the
    # default added without being asked.
    memories: list[str] = Field(default_factory=list)
    cases: list[str] = Field(default_factory=list)
    # The events this write raised, so the caller can follow the work rather
    # than poll for a state change and guess.
    events: list[str] = Field(default_factory=list)
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
    # `graph` is an axis, not a default. It answers "what else is connected to
    # this?", which is a different question from "what matches this?" -- and it
    # is only as good as the entity layer underneath it, so asking for it is a
    # decision rather than something that happens to everyone.
    match: list[Literal["vector", "lexical", "graph"]] = Field(
        default_factory=lambda: ["vector", "lexical"]
    )
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


class Excluded(BaseModel):
    """Why a record the caller *can* see did not make the answer.

    'The answer is missing something I know is in the data' has several
    completely different causes with different fixes. Naming which one applies
    turns an unfalsifiable impression into a diagnosis.
    """

    data_id: str
    reason: Literal["threshold", "not_yet_enriched"]
    score: float | None = None
    state: str | None = None


class GraphSeed(BaseModel):
    """An entity the query named, and how it was recognised.

    Reported because a result that arrived only through the graph is otherwise
    unexplainable: it does not contain the words that were searched for, and
    without the seed the reader cannot tell whether the connection was the one
    they meant. An empty list is also an answer -- it says the graph arm found
    nothing to start from, rather than that it found nothing connected.
    """

    entity_id: str
    display_name: str
    type: str
    matched_on: Literal["name", "identifier"]


class Corpus(BaseModel):
    """What the question was actually answered over (FR-SBX-7)."""

    total: int
    stored: int
    searchable: int
    enriched: int


class AskRequest(BaseModel):
    """A question, not a query string. The filter is retrieval's filter, so
    asking is scoped exactly the way searching is."""

    question: str
    filter: RetrieveFilter
    match: list[Literal["vector", "lexical", "graph"]] = Field(
        default_factory=lambda: ["vector", "lexical"]
    )
    # How many passages the model is shown. Small on purpose: the point of
    # citations is that a person can check them.
    passages: int = Field(default=8, ge=1, le=20)


class AnswerCitation(BaseModel):
    """A passage the answer actually rests on, carrying the text so the reader
    can check the claim without a second request."""

    marker: int
    data_id: str
    chunk_id: str
    text: str
    score: float
    state: str


class AskResponse(BaseModel):
    query_id: str
    question: str
    answer: str
    # False when the passages did not support an answer. The distinction the
    # user needs is between "your corpus does not say" and "your corpus says
    # this" -- collapsing them is what makes a RAG system feel untrustworthy.
    grounded: bool
    citations: list[AnswerCitation] = Field(default_factory=list)
    # How many passages were put in front of the model, against the corpus
    # counts below: "answered over 8 of 340 records, 210 of them enriched"
    # (FR-SBX-7).
    considered: int = 0
    corpus: Corpus | None = None
    excluded: list[Excluded] = Field(default_factory=list)
    # What the graph arm started from. Empty when it was not asked for.
    graph_seeds: list[GraphSeed] = Field(default_factory=list)
    model_id: str
    served_by_model: str | None = None
    generator_version: str | None = None
    # Which engine answered, and how far down the chain it was. Zero is the
    # primary. Running permanently on a fallback is invisible without this --
    # the answers keep arriving, they are just worse than the ones paid for.
    fallback_depth: int = 0
    served_by_engine: str | None = None
    answer_stored: bool = False
    latency_ms: int = 0



class RetrieveResponse(BaseModel):
    query_id: str
    results: list[Citation]
    model_id: str
    generator_version: str | None = None
    corpus: Corpus | None = None
    excluded: list[Excluded] = Field(default_factory=list)
    # What the graph arm started from. Empty when it was not asked for.
    graph_seeds: list[GraphSeed] = Field(default_factory=list)
    # ACL exclusions are deliberately absent and cannot be added: reporting
    # "3 records were hidden from you" discloses their existence, which is the
    # thing the ACL is for. The predicate runs inside the query, so the count
    # does not exist to be reported.
    acl_exclusions_reported: bool = False
