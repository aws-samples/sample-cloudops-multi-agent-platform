#!/usr/bin/env bash
# Interactive configuration, agent detection, and agent resolution

# Component flags — set by _resolve_deploy_mode()
DEPLOY_FLAG_AGENTS=true
DEPLOY_FLAG_GATEWAY=true
DEPLOY_FLAG_TOOLS=true
DEPLOY_FLAG_FRONTEND=true
DEPLOY_FLAG_COGNITO=true
DEPLOY_FLAG_MEMORY=true

# Selected tools list
SELECTED_TOOLS=()

# -----------------------------------------------------------------------------
# _detect_max_parallel_builds — pick a sane default for parallel Finch builds
# based on CPU count. Finch/Docker builds are largely I/O-bound on the layer
# cache + ECR push, so going beyond ~half the physical cores hurts more than
# it helps. Clamp between 1 and 5 (5 was the prior hardcoded default).
# -----------------------------------------------------------------------------
_detect_max_parallel_builds() {
  local cores
  if [[ "$OSTYPE" == "darwin"* ]]; then
    cores=$(sysctl -n hw.ncpu 2>/dev/null || echo 4)
  else
    cores=$(nproc 2>/dev/null || echo 4)
  fi
  local detected=$((cores / 2))
  [[ $detected -lt 1 ]] && detected=1
  [[ $detected -gt 5 ]] && detected=5
  echo "$detected"
}

_resolve_deploy_mode() {
  case "$DEPLOY_MODE" in
    full)
      DEPLOY_FLAG_AGENTS=true
      DEPLOY_FLAG_GATEWAY=true
      DEPLOY_FLAG_TOOLS=true
      DEPLOY_FLAG_FRONTEND=true
      DEPLOY_FLAG_COGNITO=true
      DEPLOY_FLAG_MEMORY=true
      ;;
    agents-only)
      DEPLOY_FLAG_AGENTS=true
      DEPLOY_FLAG_GATEWAY=true
      DEPLOY_FLAG_TOOLS=true
      DEPLOY_FLAG_FRONTEND=false
      DEPLOY_FLAG_COGNITO=true
      DEPLOY_FLAG_MEMORY=true
      ;;
    gateway-only)
      DEPLOY_FLAG_AGENTS=false
      DEPLOY_FLAG_GATEWAY=true
      DEPLOY_FLAG_TOOLS=true
      DEPLOY_FLAG_FRONTEND=false
      DEPLOY_FLAG_COGNITO=$( [ "$GATEWAY_AUTH" = "oauth" ] && echo true || echo false )
      DEPLOY_FLAG_MEMORY=false
      ;;
    tools-only)
      DEPLOY_FLAG_AGENTS=false
      DEPLOY_FLAG_GATEWAY=false
      DEPLOY_FLAG_TOOLS=true
      DEPLOY_FLAG_FRONTEND=false
      DEPLOY_FLAG_COGNITO=false
      DEPLOY_FLAG_MEMORY=false
      ;;
    *)
      die "Unknown DEPLOY_MODE: $DEPLOY_MODE (valid: full, agents-only, gateway-only, tools-only)"
      ;;
  esac
  info "Deploy mode: $DEPLOY_MODE (agents=$DEPLOY_FLAG_AGENTS gateway=$DEPLOY_FLAG_GATEWAY tools=$DEPLOY_FLAG_TOOLS frontend=$DEPLOY_FLAG_FRONTEND)"
}

_resolve_selected_tools() {
  SELECTED_TOOLS=()
  if [ -z "$DEPLOY_TOOLS" ]; then
    # All tools from tools.json
    local all_tools
    all_tools=$(.venv/bin/python -c "
import json
with open('src/lambda/mcp/tools.json') as f:
    print(' '.join(json.load(f).keys()))
" 2>/dev/null)
    for t in $all_tools; do
      SELECTED_TOOLS+=("$t")
    done
  else
    IFS=',' read -ra requested <<< "$DEPLOY_TOOLS"
    for t in "${requested[@]}"; do
      t=$(echo "$t" | xargs)
      SELECTED_TOOLS+=("$t")
    done
  fi
  info "Selected tools: ${SELECTED_TOOLS[*]}"
}

detect_lambda_tools() {
  local tools=()
  for dir in "$LAMBDA_TOOLS_DIR"/*/; do
    [ -d "$dir" ] && tools+=("$(basename "$dir")")
  done
  echo "${tools[@]:-}"
}

detect_data_collection_modules() {
  local modules=()
  for dir in "$DATA_COLLECTION_DIR"/*/; do
    [ -d "$dir" ] && modules+=("$(basename "$dir")")
  done
  echo "${modules[@]:-}"
}

# ---------------------------------------------------------------------------
# Interactive Configuration Review
# ---------------------------------------------------------------------------
show_current_config() {
  echo ""
  echo "┌─────────────────────────────────────────────────────────┐"
  echo "│           CloudOps Deployment Configuration             │"
  echo "├─────────────────────────────────────────────────────────┤"
  printf "│  %-4s %-20s %-30s │\n" "#" "Setting" "Value"
  echo "├─────────────────────────────────────────────────────────┤"
  printf "│  %-4s %-20s %-30s │\n" "1" "Project prefix" "$PROJECT_PREFIX"
  printf "│  %-4s %-20s %-30s │\n" "2" "Environment" "$ENVIRONMENT"
  printf "│  %-4s %-20s %-30s │\n" "3" "Agents" "${DEPLOY_AGENTS:-(all detected)}"
  printf "│  %-4s %-20s %-30s │\n" "4" "Memory ID" "${MEMORY_ID:-(will be created)}"
  echo "├─────────────────────────────────────────────────────────┤"
  printf "│  %-4s %-20s %-30s │\n" "" "Region (SSM)" "$AWS_REGION"
  printf "│  %-4s %-20s %-30s │\n" "" "IdP type (SSM)" "$IDP_TYPE"
  echo "│  Shared config in SSM — edit with 'make reconfigure-shared' │"
  echo "└─────────────────────────────────────────────────────────┘"
  echo ""
}

configure_agents() {
  echo ""
  echo "Available agents:"
  for agent in $ALL_AGENTS; do
    echo "  - $agent"
  done
  echo ""
  echo "Enter comma-separated agent names to deploy (or 'all' for all):"
  read -rp "Agents [current: ${DEPLOY_AGENTS:-(all)}]: " input
  [ -z "$input" ] && return

  if [ "$input" = "all" ]; then
    DEPLOY_AGENTS=""
  else
    DEPLOY_AGENTS="$input"
  fi
  save_env_var "DEPLOY_AGENTS" "$DEPLOY_AGENTS"
}

review_and_configure() {
  if [ "$AUTO_APPROVE" = true ]; then
    # Validate custom IdP settings in auto mode
    if [ "$IDP_TYPE" = "custom" ]; then
      [ -z "$CUSTOM_IDP_ISSUER_URL" ] && die "custom_idp_issuer_url required for custom IdP — set via 'make reconfigure-shared'"
      [ -z "$CUSTOM_IDP_CLIENT_ID" ] && die "custom_idp_client_id required for custom IdP — set via 'make reconfigure-shared'"
      [ -z "$CUSTOM_IDP_CLIENT_SECRET" ] && die "custom_idp_client_secret required for custom IdP — set via 'make reconfigure-shared'"
    fi
    _resolve_deploy_mode
    _resolve_selected_tools
    _resolve_selected_agents
    return
  fi

  # Non-TTY invocation (piped / CI / `make plan` from a script): accept
  # current config and move on. Blocking on `read` without a terminal hangs
  # the process forever.
  if [ ! -t 0 ]; then
    show_current_config
    info "Non-interactive stdin detected — accepting current config."
    _resolve_deploy_mode
    _resolve_selected_tools
    _resolve_selected_agents
    return
  fi

  while true; do
    show_current_config

    echo "  Enter a number to change a setting, or press Enter to proceed:"
    echo "  (To change shared values like Region or IdP: Ctrl-C and run 'make reconfigure-shared')"
    read -rp "  > " choice

    case "$choice" in
      "")  break ;;
      1)   read -rp "  Project prefix [$PROJECT_PREFIX]: " val
           [ -n "$val" ] && { PROJECT_PREFIX="$val"; save_env_var "PROJECT_PREFIX" "$val"; S3_BUCKET="${PROJECT_PREFIX}-tf-state-${ENVIRONMENT}"; DYNAMODB_TABLE="${PROJECT_PREFIX}-tf-lock-${ENVIRONMENT}"; } ;;
      2)   read -rp "  Environment [$ENVIRONMENT]: " val
           [ -n "$val" ] && { ENVIRONMENT="$val"; save_env_var "ENVIRONMENT" "$val"; S3_BUCKET="${PROJECT_PREFIX}-tf-state-${ENVIRONMENT}"; DYNAMODB_TABLE="${PROJECT_PREFIX}-tf-lock-${ENVIRONMENT}"; } ;;
      3)   configure_agents ;;
      4)   read -rp "  Memory ID [${MEMORY_ID:-(auto)}]: " val
           [ -n "$val" ] && { MEMORY_ID="$val"; save_env_var "MEMORY_ID" "$val"; } ;;
      *)   warn "Invalid option: $choice" ;;
    esac
  done

  _resolve_deploy_mode
  _resolve_selected_tools
  _resolve_selected_agents

  echo ""
  info "Configuration confirmed. Deploying..."
}

_resolve_selected_agents() {
  # In non-agent modes, skip agent resolution entirely
  if [ "$DEPLOY_FLAG_AGENTS" != true ] 2>/dev/null; then
    SELECTED_AGENTS=()
    return 0
  fi

  SELECTED_AGENTS=("$FRONTEND_AGENT")  # Always include the frontend agent

  if [ -z "$DEPLOY_AGENTS" ]; then
    # No agents specified — deploy all
    for agent in $ALL_AGENTS; do
      SELECTED_AGENTS+=("$agent")
    done
  else
    IFS=',' read -ra requested <<< "$DEPLOY_AGENTS"
    for req in "${requested[@]}"; do
      req=$(echo "$req" | xargs)  # trim whitespace

      # Check if it's a known agent
      local found=false
      for agent in $ALL_AGENTS; do
        if [ "$agent" = "$req" ]; then
          found=true
          break
        fi
      done

      if [ "$found" = false ]; then
        warn "Unrecognized agent name '$req', skipping"
        continue
      fi

      if [ "$req" = "$FRONTEND_AGENT" ]; then
        continue  # Already included
      fi

      # Add the agent itself
      SELECTED_AGENTS+=("$req")

      # Check if mid-level — add all children
      local is_mid=false
      for mid in $MID_LEVEL_AGENTS; do
        if [ "$mid" = "$req" ]; then
          is_mid=true
          break
        fi
      done

      if [ "$is_mid" = true ]; then
        local children
        children=$(get_agent_children "$req")
        for child in $children; do
          SELECTED_AGENTS+=("$child")
        done
      else
        # Leaf agent — add parent
        local parent
        parent=$(get_agent_parent "$req")
        if [ -n "$parent" ] && [ "$parent" != "$FRONTEND_AGENT" ]; then
          SELECTED_AGENTS+=("$parent")
        fi
      fi
    done
  fi

  # Deduplicate
  local unique=()
  for agent in "${SELECTED_AGENTS[@]}"; do
    local already_added=false
    for u in "${unique[@]:-}"; do
      if [ "$u" = "$agent" ]; then
        already_added=true
        break
      fi
    done
    if [ "$already_added" = false ]; then
      unique+=("$agent")
    fi
  done
  SELECTED_AGENTS=("${unique[@]}")

  return 0
}
