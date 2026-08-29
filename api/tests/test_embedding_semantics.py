"""The embedder, and the property that distinguishes a real one from a hash.

The hash embedder was never wrong, only shallow: it matched shared vocabulary.
The whole reason to pay for a learned model is that a question and the passage
answering it usually share almost no words.

The live tests are skipped without a key, because a test that silently passes
when it cannot reach the thing it tests is worse than no test.
"""

from __future__ import annotations

import os

import pytest

from memdog.inference import EmbeddingUnavailable, GeminiEmbedder, LocalHashEmbedder

pytestmark = pytest.mark.asyncio

KEY = os.environ.get("GEMINI_API_KEY", "")
live = pytest.mark.skipif(not KEY, reason="needs GEMINI_API_KEY")


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


QUESTION = "Why did the payment system go down?"
ANSWER = "An expired TLS certificate on the gateway caused a 41 minute checkout outage."
UNRELATED = "The cafeteria will be closed for renovation through September."


async def test_the_hash_embedder_cannot_connect_a_question_to_its_answer():
    """Not a criticism of it -- a statement of what it is, and of why the
    vector arm added so little while it was in place. The question and its
    answer share no content words, so a term hash has nothing to match on."""
    embedder = LocalHashEmbedder(768)
    q, a, u = await embedder.embed([QUESTION, ANSWER, UNRELATED])
    assert cosine(q, a) < 0.2
    # It cannot even reliably rank the answer above an unrelated sentence.
    assert abs(cosine(q, a) - cosine(q, u)) < 0.2


async def test_the_hash_embedder_still_matches_shared_vocabulary():
    """It is a real index, just a lexical one wearing a vector's clothes."""
    embedder = LocalHashEmbedder(768)
    a, b = await embedder.embed([
        "the payment gateway certificate expired",
        "certificate on the payment gateway had expired",
    ])
    assert cosine(a, b) > 0.7


@live
async def test_a_learned_embedder_connects_a_question_to_its_answer():
    embedder = GeminiEmbedder(KEY, "gemini-embedding-001", 768)
    question = (await embedder.embed([QUESTION], task="query"))[0]
    answer, unrelated = await embedder.embed([ANSWER, UNRELATED], task="document")

    assert cosine(question, answer) > cosine(question, unrelated), (
        "the passage that answers the question must rank above one that does not"
    )


@live
async def test_documents_and_queries_are_embedded_differently():
    """If the task type were ignored, the two vectors would be identical and
    the asymmetry would be decoration."""
    embedder = GeminiEmbedder(KEY, "gemini-embedding-001", 768)
    as_query = (await embedder.embed([QUESTION], task="query"))[0]
    as_document = (await embedder.embed([QUESTION], task="document"))[0]
    assert as_query != as_document


@live
async def test_the_dimension_is_what_was_asked_for():
    embedder = GeminiEmbedder(KEY, "gemini-embedding-001", 768)
    vectors = await embedder.embed(["a short passage"])
    assert len(vectors) == 1 and len(vectors[0]) == 768


@live
async def test_a_batch_returns_one_vector_per_text_in_order():
    """A short or reordered response would misalign every vector after the gap
    with the wrong chunk -- silent, and invisible in retrieval quality until
    someone notices answers citing the wrong passage."""
    embedder = GeminiEmbedder(KEY, "gemini-embedding-001", 768)
    texts = [f"passage number {i} about a distinct subject" for i in range(5)]
    vectors = await embedder.embed(texts)
    assert len(vectors) == len(texts)
    # Each is closest to itself when re-embedded, which order corruption breaks.
    again = await embedder.embed([texts[2]])
    best = max(range(len(vectors)), key=lambda i: cosine(again[0], vectors[i]))
    assert best == 2


async def test_the_model_id_carries_the_dimension():
    """The same model at two dimensions produces vectors that cannot be
    compared, so they must not share an identity -- otherwise the staleness
    join treats them as interchangeable and never re-embeds."""
    assert GeminiEmbedder("k", "gemini-embedding-001", 768).model_id != \
        GeminiEmbedder("k", "gemini-embedding-001", 1536).model_id


async def test_an_empty_batch_costs_nothing():
    assert await GeminiEmbedder("k", "gemini-embedding-001", 768).embed([]) == []
