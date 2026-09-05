# The repo analysis job: clone, parse with graphify, write back.
#
# **A Job, not a service.** Cloning a repository and running a full tree-sitter
# pass over it is minutes of CPU with no request to answer, and `docs/ingestion/
# uploads.md` already records what happens when that shape runs in the API: it
# autoscales on request rate, so a long CPU-bound task counts the same as a
# 20 ms write and starves the pool without triggering a scale-up.
#
# This also keeps `graphifyy` and its 37 tree-sitter grammars, plus a `git`
# binary, out of the API image -- which `pyproject.toml` has already refused
# ffmpeg for on exactly the same grounds.
FROM python:3.12-slim

# git for the clone; nothing else is needed. graphify parses locally with
# tree-sitter and reaches no network of its own.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Pinned. A parser that silently changes version under a stored snapshot would
# make two analyses of one commit disagree, which is the one property a
# per-snapshot design cannot give up.
ARG GRAPHIFY_VERSION=0.9.54
RUN pip install --no-cache-dir "graphifyy==${GRAPHIFY_VERSION}" httpx

WORKDIR /work
COPY analyze.py /work/analyze.py

# Clones land here. Cloud Run's filesystem is memory, so this is sized by the
# instance's memory rather than by disk -- the repo size guard in `repos.py`
# refuses before a clone that would not fit.
ENV CLONE_ROOT=/work/clones \
    GRAPHIFY_VERSION=${GRAPHIFY_VERSION}

ENTRYPOINT ["python", "/work/analyze.py"]
