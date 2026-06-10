FROM python:3.12-slim
WORKDIR /app

# Pip install goes on its own layer BEFORE the source copy so source edits
# (the common case) reuse the cached dep layer instead of re-resolving the
# whole transitive graph. Cache busts only when requirements.txt changes.
# Cross-arch builds on arm64 hosts emulating linux/amd64 spend ~80% of
# wall-clock time in pip, so this ordering is load-bearing for deploy speed.
COPY src/agents/frontend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r ./requirements.txt

# Run the agent as a non-root user. site-packages was written above as root
# but is world-readable, so the agent user can still import every dep.
# PYTHONDONTWRITEBYTECODE=1 prevents __pycache__ writes under /app at
# runtime, which would otherwise fail for the non-root user.
RUN useradd -r -u 1000 -m -s /usr/sbin/nologin agent

COPY --chown=agent:agent src/agents/frontend/ ./agents/frontend/
COPY --chown=agent:agent src/agents/shared/ ./agents/shared/
# AGENT_HIERARCHY_PATH is set by scripts/lib/build.sh to the sliced
# .hierarchy-<agent>.json containing only this agent's entry. Falls back to
# the full file for ad-hoc `finch build` runs outside the deploy pipeline.
#
# Safe because the supervisor discovers children at runtime from the
# DynamoDB agent registry (see shared/registry.py), NOT from sibling entries
# in this JSON. If a future code path reads `hierarchy[sibling_name]`, put
# it behind a lookup against the registry — the sliced file only has
# this agent's own entry.
ARG AGENT_HIERARCHY_PATH=src/agents/hierarchy.json
COPY --chown=agent:agent ${AGENT_HIERARCHY_PATH} ./agents/hierarchy.json
COPY --chown=agent:agent src/agents/__init__.py ./agents/__init__.py

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
EXPOSE 8080

USER agent
CMD ["opentelemetry-instrument", "uvicorn", "agents.frontend.server:app", "--host", "0.0.0.0", "--port", "8080"]
