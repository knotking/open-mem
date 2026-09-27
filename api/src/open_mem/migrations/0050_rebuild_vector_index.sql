-- Rebuild the HNSW index over `embeddings`.
--
-- Re-embedding a record deletes every one of its chunks and inserts new ones,
-- and each pass leaves the old vectors in the HNSW graph as dead tuples that
-- VACUUM has not yet reclaimed. A corpus re-embedded a few times over -- which
-- is exactly what happens while an extraction is being got right -- degrades
-- the graph until a search returns a fraction of the neighbours it should.
--
-- The failure is silent and looks nothing like an index problem: every stored
-- number stays correct (right model, right dimension, every chunk embedded),
-- lexical search keeps working, and vector search simply answers nothing. On
-- this deployment a top-40 search for a vector *already in the table* came back
-- with 11 rows, and the Bhagavad Gita corpus -- 26 records, 1352 embeddings,
-- all present and all in the queried vector space -- matched nothing at all.
--
-- DROP then CREATE rather than REINDEX: both rebuild the graph, and this form
-- is the one that also survives the index having been created with parameters
-- the current build no longer uses.
DROP INDEX IF EXISTS embeddings_vec;
CREATE INDEX embeddings_vec ON embeddings USING hnsw (embedding vector_cosine_ops);
