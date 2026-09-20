#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR_NAME="SundayNoteAgent"
VAULT_ROOT=""
WITH_PAPER_SUMMARIZER=0
MONITOR_MODE=""
MONITOR_ONLY=0
ROUTINE_TEMPLATES_MODE="managed"
OPTIONAL_CONFIG_PYTHON=""
PERSONAL_CONTEXT_HEADING='## 个性化响应'
RENDERED_AGENTS=""

cleanup() {
  if [ -n "$RENDERED_AGENTS" ] && [ -f "$RENDERED_AGENTS" ]; then
    rm -f -- "$RENDERED_AGENTS"
  fi
}

trap cleanup EXIT

usage() {
  cat <<'USAGE'
Usage:
  install.sh
  install.sh --vault-root <vault-dir>
  install.sh [--vault-root <vault-dir>] [--with-paper-summarizer]
             [--routine-templates managed|preserve]
  install.sh --vault-root <vault-dir> --with-monitor [--monitor-only]
  install.sh --vault-root <vault-dir> --without-monitor --monitor-only

Install or update SundayNoteAgent-managed files from the current checkout.
Without --vault-root, the vault root is the parent of SundayNoteAgent/.
The installer creates missing vault-local files and refreshes only managed files and plugin fields.
Paper summarizer is optional because it requires a docling-capable environment.
Routine templates default to managed. Use preserve to leave existing templates
and Calendar template settings unchanged without creating new template files.
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --vault-root)
      [ "$#" -ge 2 ] || { echo "missing value for --vault-root" >&2; exit 2; }
      VAULT_ROOT="$2"
      shift 2
      ;;
    --with-paper-summarizer)
      WITH_PAPER_SUMMARIZER=1
      shift
      ;;
    --with-monitor)
      MONITOR_MODE="install"
      shift
      ;;
    --without-monitor)
      MONITOR_MODE="uninstall"
      shift
      ;;
    --monitor-only)
      MONITOR_ONLY=1
      shift
      ;;
    --routine-templates)
      [ "$#" -ge 2 ] || { echo "missing value for --routine-templates" >&2; exit 2; }
      case "$2" in
        managed|preserve)
          ROUTINE_TEMPLATES_MODE="$2"
          ;;
        *)
          echo "invalid value for --routine-templates: $2 (expected managed or preserve)" >&2
          exit 2
          ;;
      esac
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    -*)
      echo "unknown option: $1" >&2
      usage
      exit 2
      ;;
    *)
      echo "unexpected argument: $1" >&2
      usage
      exit 2
      ;;
  esac
done

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
SCAFFOLD_DIR="$SCRIPT_DIR/scaffold"

if [ -n "$VAULT_ROOT" ]; then
  if [ ! -d "$VAULT_ROOT" ]; then
    echo "missing vault root: $VAULT_ROOT" >&2
    exit 1
  fi
  VAULT_ROOT="$(cd -- "$VAULT_ROOT" && pwd)"
else
  VAULT_ROOT="$(cd -- "$SOURCE_ROOT/.." && pwd)"
fi

configure_monitor() {
  local args=(--vault-root "$VAULT_ROOT")
  if [ "$MONITOR_MODE" = uninstall ]; then args+=(--uninstall); fi
  python3 "$SCRIPT_DIR/configure_monitor.py" "${args[@]}"
}

if [ "$MONITOR_ONLY" -eq 1 ]; then
  [ -n "$MONITOR_MODE" ] || { echo "--monitor-only requires --with-monitor or --without-monitor" >&2; exit 2; }
  configure_monitor
  exit 0
fi

require_source_file() {
  local path="$1"
  if [ ! -f "$path" ]; then
    echo "missing installer source file: $path" >&2
    exit 1
  fi
}

require_source_dir() {
  local path="$1"
  if [ ! -d "$path" ]; then
    echo "missing installer source directory: $path" >&2
    exit 1
  fi
}

preflight_container_dir() {
  local path="$1"
  if [ -L "$path" ]; then
    echo "installer container is a symlink; refusing to write through it: $path" >&2
    exit 1
  fi
  if [ -e "$path" ] && [ ! -d "$path" ]; then
    echo "installer container exists and is not a directory: $path" >&2
    exit 1
  fi
}

preflight_managed_file() {
  local path="$1"
  if [ -L "$path" ]; then
    echo "managed file destination is a symlink; refusing to replace it: $path" >&2
    exit 1
  fi
  if [ -e "$path" ] && [ ! -f "$path" ]; then
    echo "managed file destination exists and is not a file: $path" >&2
    exit 1
  fi
}

preflight_local_file() {
  local path="$1"
  if [ -L "$path" ]; then
    return
  fi
  if [ -e "$path" ] && [ ! -f "$path" ]; then
    echo "vault-local file destination exists and is not a file: $path" >&2
    exit 1
  fi
}

preflight_append_file() {
  local path="$1"
  if [ -L "$path" ]; then
    echo "vault-local append target is a symlink; refusing to write through it: $path" >&2
    exit 1
  fi
  if [ -e "$path" ] && [ ! -f "$path" ]; then
    echo "vault-local append target exists and is not a file: $path" >&2
    exit 1
  fi
}

preflight_managed_dir() {
  local path="$1"
  if [ -e "$path" ] && [ ! -d "$path" ] && [ ! -L "$path" ]; then
    echo "managed directory destination exists and is not a directory: $path" >&2
    exit 1
  fi
}

copy_managed_file() {
  local src="$1"
  local dst="$2"
  mkdir -p "$(dirname -- "$dst")"
  cp "$src" "$dst"
  chmod u+rw "$dst" 2>/dev/null || true
}

copy_if_missing() {
  local src="$1"
  local dst="$2"
  if [ -e "$dst" ] || [ -L "$dst" ]; then
    return
  fi
  mkdir -p "$(dirname -- "$dst")"
  cp "$src" "$dst"
  chmod u+rw "$dst" 2>/dev/null || true
}

copy_managed_dir() {
  local src="$1"
  local dst="$2"
  if [ -L "$dst" ]; then
    rm -f "$dst"
  fi
  mkdir -p "$dst"
  cp -R "$src/." "$dst/"
}

prepare_managed_agents() {
  local template="$SCAFFOLD_DIR/AGENTS.md"
  local target="$VAULT_ROOT/AGENTS.md"
  local line
  local state=before
  local heading_count=0
  local has_personal_context=0

  heading_count="$(grep -Exc -- "${PERSONAL_CONTEXT_HEADING}"$'\r?' "$template" || true)"
  if [ "$heading_count" -ne 0 ]; then
    echo "managed AGENTS.md scaffold must not contain a personal context section: $template" >&2
    return 1
  fi

  if [ -f "$target" ]; then
    heading_count="$(grep -Exc -- "${PERSONAL_CONTEXT_HEADING}"$'\r?' "$target" || true)"
    if [ "$heading_count" -eq 1 ]; then
      has_personal_context=1
    elif [ "$heading_count" -gt 1 ]; then
      echo "personal context heading must appear at most once: $target" >&2
      return 1
    fi
  fi

  RENDERED_AGENTS="$(mktemp)"
  cp -p "$template" "$RENDERED_AGENTS"
  if [ "$has_personal_context" -eq 1 ]; then
    if [ -s "$RENDERED_AGENTS" ] && [ -n "$(tail -c 1 "$RENDERED_AGENTS")" ]; then
      printf '\n' >> "$RENDERED_AGENTS"
    fi
    printf '\n' >> "$RENDERED_AGENTS"
    while IFS= read -r line || [ -n "$line" ]; do
      if [ "${line%$'\r'}" = "$PERSONAL_CONTEXT_HEADING" ]; then
        state=inside
      fi
      if [ "$state" = inside ]; then
        printf '%s\n' "$line" >> "$RENDERED_AGENTS"
      fi
    done < "$target"
  fi
}

ensure_vault_dirs() {
  mkdir -p \
    "$VAULT_ROOT/.agents" \
    "$VAULT_ROOT/.import_files" \
    "$VAULT_ROOT/10_原始材料" \
    "$VAULT_ROOT/20_每日记录" \
    "$VAULT_ROOT/21_每周记录" \
    "$VAULT_ROOT/22_每月记录" \
    "$VAULT_ROOT/23_项目复盘" \
    "$VAULT_ROOT/30_知识库" \
    "$VAULT_ROOT/40_个人写作" \
    "$VAULT_ROOT/个人模板" \
    "$VAULT_ROOT/assets/figures"

  touch \
    "$VAULT_ROOT/.import_files/.gitkeep" \
    "$VAULT_ROOT/10_原始材料/.gitkeep" \
    "$VAULT_ROOT/20_每日记录/.gitkeep" \
    "$VAULT_ROOT/21_每周记录/.gitkeep" \
    "$VAULT_ROOT/22_每月记录/.gitkeep" \
    "$VAULT_ROOT/23_项目复盘/.gitkeep" \
    "$VAULT_ROOT/30_知识库/.gitkeep" \
    "$VAULT_ROOT/40_个人写作/.gitkeep" \
    "$VAULT_ROOT/个人模板/.gitkeep" \
    "$VAULT_ROOT/assets/figures/.gitkeep"
}

ensure_personal_context_file() {
  local target="$VAULT_ROOT/个人上下文.md"

  if [ -e "$target" ] || [ -L "$target" ]; then
    return
  fi

  cp -- "$SOURCE_ROOT/skills/sunday-note-context/assets/个人上下文.md" "$target"
}

ensure_syncthing_ignores() {
  local target="$VAULT_ROOT/.stignore"
  local pattern

  for pattern in "/$PROJECT_DIR_NAME" "/.import_files"; do
    if [ -f "$target" ] && grep -Fxq -- "$pattern" "$target"; then
      continue
    fi
    if [ -s "$target" ] && [ -n "$(tail -c 1 "$target")" ]; then
      printf '\n' >> "$target"
    fi
    printf '%s\n' "$pattern" >> "$target"
  done
}

paper_skill_path="$VAULT_ROOT/.agents/skills/paper-summarizer"
install_paper_summarizer=0
if [ "$WITH_PAPER_SUMMARIZER" -eq 1 ] || [ -e "$paper_skill_path" ] || [ -L "$paper_skill_path" ]; then
  install_paper_summarizer=1
fi

require_source_file "$SCAFFOLD_DIR/AGENTS.md"
require_source_file "$SCAFFOLD_DIR/首页.md"
require_source_file "$SCAFFOLD_DIR/.gitignore"
require_source_file "$SOURCE_ROOT/config/quickadd-rollups.json"
require_source_file "$SOURCE_ROOT/config/obsidian/calendar.json"
require_source_file "$SOURCE_ROOT/config/obsidian/quickadd.json"
require_source_file "$SCRIPT_DIR/configure_optional_integrations.py"
if [ "$ROUTINE_TEMPLATES_MODE" = managed ]; then
  require_source_file "$SOURCE_ROOT/templates/每日记录.md"
  require_source_file "$SOURCE_ROOT/templates/每周记录.md"
  require_source_file "$SOURCE_ROOT/templates/每月记录.md"
fi
require_source_dir "$SOURCE_ROOT/automation/quickadd"
require_source_dir "$SOURCE_ROOT/skills/sunday-note-context"
require_source_file "$SOURCE_ROOT/skills/sunday-note-context/assets/个人上下文.md"
require_source_dir "$SOURCE_ROOT/skills/sunday-note-ingest"
require_source_dir "$SOURCE_ROOT/skills/sunday-note-lint"
require_source_dir "$SOURCE_ROOT/skills/sunday-note-query"
if [ "$install_paper_summarizer" -eq 1 ]; then
  require_source_dir "$SOURCE_ROOT/skills/paper-summarizer"
fi

if [ ! -d "$VAULT_ROOT/$PROJECT_DIR_NAME" ]; then
  echo "missing $PROJECT_DIR_NAME directory under vault root: $VAULT_ROOT" >&2
  exit 1
fi

if command -v python3 >/dev/null 2>&1; then
  OPTIONAL_CONFIG_PYTHON="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
  OPTIONAL_CONFIG_PYTHON="$(command -v python)"
fi

preflight_container_dir "$VAULT_ROOT/.agents"
preflight_container_dir "$VAULT_ROOT/.agents/skills"
preflight_container_dir "$VAULT_ROOT/.sunday-note-agent"
preflight_container_dir "$VAULT_ROOT/.sunday-note-agent/config"

preflight_managed_file "$VAULT_ROOT/AGENTS.md"
preflight_local_file "$VAULT_ROOT/首页.md"
preflight_local_file "$VAULT_ROOT/.gitignore"
preflight_append_file "$VAULT_ROOT/.stignore"
preflight_local_file "$VAULT_ROOT/.sunday-note-agent/config/quickadd-rollups.json"
preflight_local_file "$VAULT_ROOT/个人上下文.md"
if [ "$ROUTINE_TEMPLATES_MODE" = managed ]; then
  preflight_local_file "$VAULT_ROOT/个人模板/每日记录.md"
  preflight_managed_file "$VAULT_ROOT/个人模板/每周记录.md"
  preflight_managed_file "$VAULT_ROOT/个人模板/每月记录.md"
fi

preflight_managed_dir "$VAULT_ROOT/.agents/skills/sunday-note-ingest"
preflight_managed_dir "$VAULT_ROOT/.agents/skills/sunday-note-lint"
preflight_managed_dir "$VAULT_ROOT/.agents/skills/sunday-note-query"
preflight_managed_dir "$VAULT_ROOT/.agents/skills/sunday-note-context"
if [ "$install_paper_summarizer" -eq 1 ]; then
  preflight_managed_dir "$paper_skill_path"
fi
prepare_managed_agents

ensure_vault_dirs
copy_managed_file "$RENDERED_AGENTS" "$VAULT_ROOT/AGENTS.md"
copy_if_missing "$SCAFFOLD_DIR/首页.md" "$VAULT_ROOT/首页.md"
copy_if_missing "$SCAFFOLD_DIR/.gitignore" "$VAULT_ROOT/.gitignore"
ensure_syncthing_ignores
if [ "$ROUTINE_TEMPLATES_MODE" = managed ]; then
  copy_if_missing "$SOURCE_ROOT/templates/每日记录.md" "$VAULT_ROOT/个人模板/每日记录.md"
  copy_managed_file "$SOURCE_ROOT/templates/每周记录.md" "$VAULT_ROOT/个人模板/每周记录.md"
  copy_managed_file "$SOURCE_ROOT/templates/每月记录.md" "$VAULT_ROOT/个人模板/每月记录.md"
else
  echo "已保留父 vault 的 Routine 模板与 Calendar 模板设置。"
fi
ensure_personal_context_file

copy_managed_dir "$SOURCE_ROOT/skills/sunday-note-context" "$VAULT_ROOT/.agents/skills/sunday-note-context"
copy_managed_dir "$SOURCE_ROOT/skills/sunday-note-ingest" "$VAULT_ROOT/.agents/skills/sunday-note-ingest"
copy_managed_dir "$SOURCE_ROOT/skills/sunday-note-lint" "$VAULT_ROOT/.agents/skills/sunday-note-lint"
copy_managed_dir "$SOURCE_ROOT/skills/sunday-note-query" "$VAULT_ROOT/.agents/skills/sunday-note-query"
if [ "$install_paper_summarizer" -eq 1 ]; then
  copy_managed_dir "$SOURCE_ROOT/skills/paper-summarizer" "$paper_skill_path"
  rm -f \
    "$paper_skill_path/assets/embodied_ai_terminology.json" \
    "$paper_skill_path/scripts/write_summary_status.py"
fi
copy_if_missing "$SOURCE_ROOT/config/quickadd-rollups.json" "$VAULT_ROOT/.sunday-note-agent/config/quickadd-rollups.json"

if [ -n "$OPTIONAL_CONFIG_PYTHON" ]; then
  optional_config_args=(--vault-root "$VAULT_ROOT")
  if [ "$ROUTINE_TEMPLATES_MODE" = preserve ]; then
    optional_config_args+=(--skip-calendar)
  fi
  if ! "$OPTIONAL_CONFIG_PYTHON" "$SCRIPT_DIR/configure_optional_integrations.py" "${optional_config_args[@]}"; then
    echo "可选集成配置失败；核心安装已完成，Calendar/QuickAdd 未全部配置。" >&2
  fi
else
  echo "可选工作流未配置：Calendar Weekly 创建（未找到 python3 或 python；核心安装已完成）。"
  echo "可选工作流未配置：QuickAdd Routine 自动化（未找到 python3 或 python；核心安装已完成）。"
fi
echo "Installed or updated Sunday Note vault at: $VAULT_ROOT"
if [ -n "$MONITOR_MODE" ]; then configure_monitor; fi
if [ "$ROUTINE_TEMPLATES_MODE" = managed ]; then
  echo "Managed rules, skills, and Routine files were refreshed from: $PROJECT_DIR_NAME"
else
  echo "Managed rules and skills were refreshed from: $PROJECT_DIR_NAME"
fi
echo "Vault-local content and unmanaged configuration were preserved."
echo "安装期间应关闭 Obsidian；如果刚才正在运行，请退出后重新运行安装器，再启动 Obsidian。"
