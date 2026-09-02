# OpenClaw on Cloud Run, from the official published image.
#
# Pinned to a release rather than `latest` -- upstream's own Docker guidance
# says to, and a gateway that silently changes version under a running demo is
# not worth the convenience.
#
# The slim variant is deliberate: `-browser` is 0.87GB against 0.46GB, and the
# browser toolset is denied below anyway. Image size is cold-start latency on
# Cloud Run, so the variant that carries a Chromium we forbid is the wrong one.
FROM openclaw/openclaw:2026.8.2-slim

# The published entrypoint is `tini -s --` with CMD `node openclaw.mjs gateway`.
# cloudrun-boot.sh takes its place, seeds the config, and then execs tini with
# the same argv -- so the process tree and signal handling are unchanged.
COPY --chmod=0755 cloudrun-boot.sh /home/node/cloudrun-boot.sh
ENTRYPOINT ["/home/node/cloudrun-boot.sh"]
CMD ["node", "openclaw.mjs", "gateway"]
