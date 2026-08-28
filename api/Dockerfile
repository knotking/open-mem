# Slim, non-root, no build toolchain in the runtime layer.
FROM python:3.12-slim AS build
WORKDIR /build
RUN pip install --no-cache-dir --upgrade pip build
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip wheel --no-cache-dir --wheel-dir /wheels .

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN useradd --uid 10001 --create-home memdog
COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels memdog-api && rm -rf /wheels
# The blob store is a filesystem path in this variant; GCS swaps in behind the
# same interface. Either way the process does not need to write anywhere else.
RUN mkdir -p /data/blobs && chown -R memdog:memdog /data
USER memdog
ENV BLOB_ROOT=/data/blobs
EXPOSE 8080
CMD ["uvicorn", "memdog.app:app", "--host", "0.0.0.0", "--port", "8080"]
