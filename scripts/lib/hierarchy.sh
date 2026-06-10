#!/usr/bin/env bash
# Agent hierarchy — loaded dynamically from hierarchy.json

HIERARCHY_FILE="src/agents/hierarchy.json"
ALL_AGENTS=""
MID_LEVEL_AGENTS=""
FRONTEND_AGENT=""

_load_hierarchy() {
  if [ ! -f "$HIERARCHY_FILE" ]; then
    die "Agent hierarchy file not found: $HIERARCHY_FILE"
  fi
  eval "$(.venv/bin/python -c "
import json
with open('$HIERARCHY_FILE') as f:
    h = json.load(f)
agents = list(h.keys())
# Find the frontend agent
frontend = next((name for name, cfg in h.items() if cfg.get('type') == 'frontend'), 'supervisor')
# Mid-level = agents that are direct children of the frontend agent
mid_level = [name for name, cfg in h.items()
             if name != frontend and name in h.get(frontend, {}).get('children', [])]
print(f'ALL_AGENTS=\"{\" \".join(agents)}\"')
print(f'MID_LEVEL_AGENTS=\"{\" \".join(mid_level)}\"')
print(f'FRONTEND_AGENT=\"{frontend}\"')
")"
}

get_agent_parent() {
  .venv/bin/python -c "
import json
with open('$HIERARCHY_FILE') as f:
    h = json.load(f)
for name, cfg in h.items():
    if '$1' in cfg.get('children', []):
        print(name); break
else:
    print('')
" 2>/dev/null
}

get_agent_children() {
  .venv/bin/python -c "
import json
with open('$HIERARCHY_FILE') as f:
    h = json.load(f)
print(' '.join(h.get('$1', {}).get('children', [])))
" 2>/dev/null
}

get_agent_dir() {
  .venv/bin/python -c "
import json
with open('$HIERARCHY_FILE') as f:
    h = json.load(f)
d = h.get('$1', {}).get('dir', '')
print('src/' + d if d else '')
" 2>/dev/null
}

get_agent_type() {
  .venv/bin/python -c "
import json
with open('$HIERARCHY_FILE') as f:
    h = json.load(f)
print(h.get('$1', {}).get('type', ''))
" 2>/dev/null
}

# Per-agent image tracking (temp files for bash 3.x compat)
AGENT_IMAGES_DIR=""
_init_agent_tracking() {
  AGENT_IMAGES_DIR=$(mktemp -d)
  trap "rm -rf '$AGENT_IMAGES_DIR'" EXIT
}
set_agent_image() { echo "$2" > "$AGENT_IMAGES_DIR/$1.image"; }
get_agent_image() { cat "$AGENT_IMAGES_DIR/$1.image" 2>/dev/null || echo ""; }
set_agent_changed() { echo "$2" > "$AGENT_IMAGES_DIR/$1.changed"; }
get_agent_changed() { cat "$AGENT_IMAGES_DIR/$1.changed" 2>/dev/null || echo "false"; }
