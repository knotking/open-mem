# The published Hermes Agent image, with only what Cloud Run needs added.
#
# Deliberately not a rebuild from source: the upstream Dockerfile compiles
# SQLite, installs s6-overlay, Node 26 and Playwright, and its own comments put
# that at 15-45 minutes. The published image is the same artifact, already
# built, and pinning it by tag keeps this deploy reproducible without owning
# their build.
FROM nousresearch/hermes-agent:latest

# Onboarding is interactive and there is nobody here to answer it.
ENV HERMES_NONINTERACTIVE=1

# Tool restriction is NOT set here. There is no HERMES_DISABLED_TOOLSETS
# variable -- the mechanism is `platform_toolsets` in config.yaml, which
# cloudrun-boot.sh writes. An env var that does not exist is the worst of both
# worlds: it reads as a locked-down deploy and leaves terminal access on.

COPY --chmod=0755 cloudrun-boot.sh /opt/hermes/docker/cloudrun-boot.sh
ENTRYPOINT ["/opt/hermes/docker/cloudrun-boot.sh"]
CMD ["gateway"]
