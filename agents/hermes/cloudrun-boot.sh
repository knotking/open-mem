#!/bin/sh
# Seed $HERMES_HOME from the environment, then hand off to the image's own
# entrypoint untouched.
#
# Hermes is built for a VPS with a persistent volume: it is configured once,
# interactively, into ~/.hermes and expects that to still be there tomorrow.
# Cloud Run gives it neither -- the filesystem is ephemeral and there is nobody
# to answer a prompt -- so the configuration has to be reconstructed on every
# cold start from environment and secrets. That is the whole job of this file.
#
# It runs before /init, as root, because the image's stage2 hook needs to
# usermod and chown after us, and because the config must exist before the
# gateway reads it.
set -eu

: "${HERMES_HOME:=/opt/data}"
mkdir -p "$HERMES_HOME"

# Cloud Run tells the container which port to listen on and will kill it if it
# listens somewhere else. Hermes defaults to 8642, so this is not optional.
PORT="${PORT:-8080}"

# The MCP block is written only when a corpus is configured, so a deploy that
# forgot the key produces an agent with no tools rather than one holding a
# broken server definition it will retry against all session.
MCP_BLOCK=""
if [ -n "${MEMDOG_API_URL:-}" ] && [ -n "${MEMDOG_API_KEY:-}" ]; then
  MCP_BLOCK=$(printf 'mcp_servers:\n  mem_dog:\n    url: "%s/api/v1/mcp"\n    headers:\n      Authorization: "Bearer %s"\n' \
    "${MEMDOG_API_URL%/}" "$MEMDOG_API_KEY")
fi

# `api_server` is the platform that matters here and it is NOT `cli`.
#
# Found the hard way: with only `cli` narrowed, a live run called `terminal`
# with `env | grep -i mem` -- arbitrary shell on a public endpoint, and an
# environment dump that carries MEMDOG_API_KEY straight into the model
# provider's context. The restriction was real and it was applied to a surface
# nothing was running on.
#
# Two things made that easy to get wrong, and both are worth naming:
# `hermes_cli/platforms.py` registers `api_server` with its own default toolset
# (`hermes-api-server`), while `hermes_cli/skills_config.py` filters
# `api_server` out of the platform list the docs and `hermes tools list` render.
# So the surface you can inspect and the surface that executes are different
# ones, and the inspectable one reports success.
#
# Every platform is pinned below, not just the two that are known to dispatch.
# A default that turns terminal back on should have to get past an explicit
# line here.
cat > "$HERMES_HOME/config.yaml" <<EOF
# Generated at boot by cloudrun-boot.sh. Edits here do not survive a restart --
# change the deploy, not this file.
model:
  default: "${HERMES_MODEL:-hermes-4-405b}"
  provider: "${HERMES_PROVIDER:-nous-api}"

# WAL on an ephemeral container filesystem buys durability that cannot outlive
# the instance anyway, and Hermes will not live-downgrade a database already
# opened in WAL -- so it is set here, before the first open, rather than
# discovered as a fallback.
database:
  journal_mode: "delete"

platform_toolsets:
  api_server: [${HERMES_TOOLSETS:-todo}]
  cli: [${HERMES_TOOLSETS:-todo}]
  local: [${HERMES_TOOLSETS:-todo}]
  webhook: [${HERMES_TOOLSETS:-todo}]

${MCP_BLOCK}
EOF

# Secrets go to .env, which is the file Hermes reads them from, and never to
# config.yaml -- `hermes dump` and the dashboard both render config.
# The provider decides which variable name the key must arrive under, and
# getting it wrong is silent: Hermes finds no credential for the configured
# provider and fails at the first turn, not at boot. So the deploy mounts one
# secret as MODEL_API_KEY and the name is derived here from the provider that
# is actually configured -- which is what lets the same image run on Nous
# Portal or OpenRouter without a second build.
case "${HERMES_PROVIDER:-nous-api}" in
  nous-api|nous) KEY_VAR=NOUS_API_KEY ;;
  openrouter)    KEY_VAR=OPENROUTER_API_KEY ;;
  anthropic)     KEY_VAR=ANTHROPIC_API_KEY ;;
  gemini)        KEY_VAR=GEMINI_API_KEY ;;
  zai)           KEY_VAR=GLM_API_KEY ;;
  ollama-cloud)  KEY_VAR=OLLAMA_API_KEY ;;
  deepinfra)     KEY_VAR=DEEPINFRA_API_KEY ;;
  # Not a safe default so much as the least-wrong one: every remaining
  # provider in the catalog is OpenAI-compatible and reads OPENAI_API_KEY.
  *)             KEY_VAR=OPENAI_API_KEY ;;
esac
MODEL_API_KEY="${MODEL_API_KEY:-${NOUS_API_KEY:-}}"

{
  [ -n "${MODEL_API_KEY:-}" ] && printf '%s=%s\n' "$KEY_VAR" "$MODEL_API_KEY"
  [ -n "${API_SERVER_KEY:-}" ] && printf 'API_SERVER_KEY=%s\n' "$API_SERVER_KEY"
  printf 'API_SERVER_ENABLED=true\n'
  printf 'API_SERVER_HOST=0.0.0.0\n'
  printf 'API_SERVER_PORT=%s\n' "$PORT"
} > "$HERMES_HOME/.env"
chmod 0600 "$HERMES_HOME/.env"

# The image runs services as uid 10000. Files this script wrote as root would
# otherwise be unreadable to the process that needs them.
chown -R 10000:10000 "$HERMES_HOME" 2>/dev/null || true

echo "cloudrun-boot: seeded ${HERMES_HOME} (port ${PORT}, model ${HERMES_MODEL:-hermes-4-405b}, provider ${HERMES_PROVIDER:-nous-api}, key_var ${KEY_VAR}$([ -n "${MODEL_API_KEY:-}" ] && echo " set" || echo " MISSING"), mcp=$([ -n "$MCP_BLOCK" ] && echo mem_dog || echo none))"

exec /opt/hermes/docker/entrypoint-dispatch.sh "$@"
