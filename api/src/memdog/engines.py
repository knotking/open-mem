"""Which engine serves this org, resolved at request time.

Every engine in this codebase already sits behind a Protocol, and every one of
them was still chosen once, at boot, from an environment variable. So the seams
were polymorphic and the *selection* was not: one deployment meant one
extractor and one answerer for every organization, and the `model_assignments`
table -- which exists precisely to let an org choose -- was consulted by the
multimodal path and nothing else.

This is the piece that makes the assignment mean something. Given a purpose and
an org, it resolves the assignment, builds the engine that assignment names, and
caches it.

Three things are load-bearing:

**The deployment default is not a failure case.** Most installations will never
assign anything, and the shipped configuration has to work. An org with no
assignment gets exactly what it got before.

**Engines are cached by identity, not by org.** Two orgs assigned the same model
on the same provider share one client; the cache key is what actually determines
behaviour -- the engine row and the model id -- so it cannot hand back a client
built from another org's credential.

**Embedding is deliberately absent.** Assignment resolution stops at generation.
Two orgs extracting with different models produce artifacts that each record
which model made them, and that is recoverable. Two orgs *embedding* with
different models write vectors from different spaces into one index, and the
only signal is that ranking gets quietly worse. `assign()` already refuses an
embedding assignment that is not `data_type: '*'`; making the engine swappable
per request would route around the reason for that rule. It stays a deployment
decision until there is a per-org index to go with it.
"""

from __future__ import annotations

import logging

import asyncpg

from .crypto import CryptoUnavailable, Envelope

log = logging.getLogger(__name__)


class EngineRegistry:
    """Resolves and caches the engines an org's work should run on.

    Holds the deployment-configured engines as the floor, so a resolution that
    finds nothing returns the same object the service booted with rather than
    constructing a second identical one.
    """

    def __init__(self, settings, envelope: Envelope, *, extractor, answerer) -> None:
        self._settings = settings
        self._envelope = envelope
        # What the deployment configured. Returned whenever an org has said
        # nothing, which is the common case and not a degraded one.
        self._default_extractor = extractor
        self._default_answerer = answerer
        self._cache: dict[tuple[str, str, str], object] = {}

    # -- resolution ---------------------------------------------------------

    async def extractor_for(self, pool: asyncpg.Pool, *, org_id: str, data_type: str):
        """The extractor this org assigned for this data type, or the default."""
        return await self._resolve(
            pool, purpose="extraction", org_id=org_id, data_type=data_type,
            default=self._default_extractor, kind="extraction",
        )

    async def answerer_for(self, pool: asyncpg.Pool, *, org_id: str):
        """The answerer for this org, following its **extraction** assignment.

        Not a purpose of its own, deliberately. `build_answerer` already ties
        chat to the extraction engine's configuration, because running the
        answerer on a different provider would mean two `allowed_providers`
        decisions where the org made one -- and now that the allow-list is
        actually enforced, that argument is stronger rather than weaker.

        So this makes the existing coupling per-org instead of per-deployment.
        It does not loosen it.
        """
        return await self._resolve(
            pool, purpose="extraction", org_id=org_id, data_type="*",
            default=self._default_answerer, kind="answer",
        )

    async def _resolve(self, pool, *, purpose: str, org_id: str, data_type: str,
                       default, kind: str):
        from .models import ModelError, resolve_model

        try:
            assignment = await resolve_model(
                pool, purpose=purpose, data_type=data_type,
                org_id=org_id, default_model=default.model_id,
            )
        except ModelError:
            # Candidacy refused the assigned model -- a regulated type against a
            # remotely hosted one, or a provider the org's allow-list excludes.
            # Re-raised for the caller to record, because silently falling back
            # to the deployment default would route the content the rule exists
            # to protect.
            raise
        if assignment.source != "assignment" or assignment.engine_id is None:
            # No assignment, or one naming no engine to reach it through. The
            # deployment default is the answer, not a fallback from a failure.
            return default
        built = await self._build(pool, assignment, kind=kind)
        return built or default

    # -- construction -------------------------------------------------------

    async def _build(self, pool, assignment, *, kind: str):
        # Keyed on what actually determines behaviour -- the engine row, the
        # model, and which kind of client is wanted. Not on the org: two orgs
        # assigned the same model through the same engine share one client,
        # and one assigned a different engine can never be handed it.
        key = (assignment.engine_id, assignment.model_id, kind)
        if key in self._cache:
            return self._cache[key]

        row = await pool.fetchrow(
            "SELECT org_id, provider, base_url, credential_ct, enabled "
            "FROM engines WHERE engine_id = $1",
            assignment.engine_id,
        )
        if row is None or not row["enabled"]:
            return None

        credential = None
        if row["credential_ct"]:
            try:
                credential = self._envelope.decrypt(
                    bytes(row["credential_ct"]), aad=row["org_id"].encode()
                ).decode()
            except (CryptoUnavailable, Exception) as exc:  # noqa: BLE001
                # Fails closed and says so. An engine whose credential cannot be
                # read is not an engine that should quietly be replaced by the
                # deployment's own key -- that would spend the platform's
                # credential on work an org asked to run on theirs.
                log.error("engine %s credential unreadable: %r",
                          assignment.engine_id, exc)
                return None

        engine = self._construct(
            kind=kind, provider=row["provider"], model_id=assignment.model_id,
            base_url=row["base_url"], credential=credential,
        )
        if engine is None:
            log.warning("no %s implementation for provider %r",
                        kind, row["provider"])
            return None
        self._cache[key] = engine
        return engine

    def _construct(self, *, kind: str, provider: str, model_id: str,
                   base_url: str | None, credential: str | None):
        """Provider plus kind to a concrete engine.

        A dict of constructors rather than a chain of branches, so a new
        provider is an entry and an unknown one is a `None` the caller reports
        rather than an exception nobody expected.
        """
        from .chat import GeminiAnswerer
        from .extraction import GeminiExtractor, OllamaExtractor

        builders = {
            ("extraction", "google"): lambda: GeminiExtractor(credential or "", model_id),
            ("extraction", "gemini"): lambda: GeminiExtractor(credential or "", model_id),
            ("extraction", "ollama"): lambda: OllamaExtractor(
                model_id, base_url or self._settings.ollama_url),
            ("answer", "google"): lambda: GeminiAnswerer(credential or "", model_id),
            ("answer", "gemini"): lambda: GeminiAnswerer(credential or "", model_id),
        }
        build = builders.get((kind, provider))
        return build() if build else None
