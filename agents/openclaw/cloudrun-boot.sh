#!/bin/sh
# Seed OpenClaw's config from the environment, then hand off to its own entrypoint.
#
# Same shape as agents/hermes: the runtime expects a persistent ~/.openclaw
# configured once by a human, and Cloud Run gives it an ephemeral filesystem and
# nobody to ask. So the config is rebuilt on every cold start.
set -eu

: "${OPENCLAW_CONFIG_DIR:=/home/node/.openclaw}"
mkdir -p "$OPENCLAW_CONFIG_DIR"

# Cloud Run dictates the port and kills a container that listens elsewhere.
PORT="${PORT:-8080}"

# `bind` takes a MODE, not a host. The docs are explicit that host aliases
# ("0.0.0.0", "localhost") are rejected here, and the default `loopback` listens
# on 127.0.0.1 inside the container -- which on Cloud Run means the port never
# answers and the revision fails its startup probe for no visible reason.
# Written to a temp file and moved into place. The heredoc below is unquoted --
# it has to be, for ${PORT} and ${MEMDOG_API_URL} to expand -- which means the
# shell also evaluates anything else it recognises. A backtick inside a JSON
# comment here executed `openclaw config validate` as a command substitution
# while this very file sat truncated, logging a JSON5 parse failure 107ms before
# the script announced success. Never use backticks in this heredoc; the mv
# additionally means no reader can ever observe a half-written config.
cat > "$OPENCLAW_CONFIG_DIR/openclaw.json.tmp" <<EOF
{
  "gateway": {
    // Required at startup even though the schema treats it as optional: the
    // gateway refuses to boot without it, reporting the absence as
    // "suspicious or clobbered config". Schema validation passes regardless,
    // so this is only reachable by actually starting the gateway.
    "mode": "local",
    "port": ${PORT},
    "bind": "lan",

    "terminal": { "enabled": false }
  },

  "mcp": {
    "servers": {
      "mem_dog": {
        "url": "${MEMDOG_API_URL%/}/api/v1/mcp",
        "transport": "streamable-http",
        "requestTimeoutMs": 30000,
        "connectionTimeoutMs": 10000,
        "headers": { "Authorization": "Bearer \${MEMDOG_API_KEY}" }
      }
    }
  },

  "models": {
    "providers": {
      "google": { "agentRuntime": { "id": "openclaw" } }
    }
  },

  "agents": {
    "defaults": {
      "model": "${OPENCLAW_MODEL:-google/gemini-3.5-flash}"
    },
    "entries": {
      "main": {
        "tools": {
          "deny": ["exec", "write", "edit", "apply_patch", "browser", "canvas", "read"]
        }
      }
    }
  }
}
EOF
mv "$OPENCLAW_CONFIG_DIR/openclaw.json.tmp" "$OPENCLAW_CONFIG_DIR/openclaw.json"

echo "openclaw-boot: seeded ${OPENCLAW_CONFIG_DIR} (port ${PORT}, bind lan, model ${OPENCLAW_MODEL:-google/gemini-3.5-flash}, mcp=$([ -n "${MEMDOG_API_URL:-}" ] && echo mem_dog || echo none), terminal off)"

exec tini -s -- "$@"
