#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR_NAME="SundayNoteAgent"
VAULT_ROOT=""
INSTALL_MODE=""
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
             [--mode personal|work]
             [--routine-templates managed|preserve]
  install.sh --vault-root <vault-dir> --with-monitor [--monitor-only]
  install.sh --vault-root <vault-dir> --without-monitor --monitor-only

Install or update SundayNoteAgent-managed files from the current checkout.
Without --vault-root, the vault root is the parent of SundayNoteAgent/.
The installer creates missing vault-local files and refreshes only managed files and plugin fields.
Paper summarizer is optional and uses the agent's available PDF reading tools.
Mode defaults to personal on first install and is remembered for updates.
Work mode creates only work partitions, work templates and work entry points.
Routine templates default to managed. Use preserve to leave existing templates
and Calendar template settings unchanged without creating new template files.
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --mode)
      [ "$#" -ge 2 ] || { echo "missing value for --mode" >&2; exit 2; }
      case "$2" in personal|work) INSTALL_MODE="$2" ;; *) echo "invalid mode: $2" >&2; exit 2 ;; esac
      shift 2
      ;;
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

mode_file="$VAULT_ROOT/.sunday-note-agent/install-mode"
if [ -z "$INSTALL_MODE" ]; then
  if [ -f "$mode_file" ]; then INSTALL_MODE="$(< "$mode_file")"; else INSTALL_MODE=personal; fi
fi
case "$INSTALL_MODE" in personal|work) ;; *) echo "invalid saved install mode: $INSTALL_MODE" >&2; exit 2 ;; esac
TEMPLATE_DIR=个人模板
TEMPLATE_SOURCE="$SOURCE_ROOT/templates"
CORE_SKILLS=(sunday-note-ingest sunday-note-lint sunday-note-query)
if [ "$INSTALL_MODE" = work ]; then
  TEMPLATE_DIR=工作模板
  TEMPLATE_SOURCE="$SOURCE_ROOT/templates/work"
else
  CORE_SKILLS+=(sunday-note-context)
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
  if [ "$INSTALL_MODE" = work ] && [ "$has_personal_context" -eq 1 ]; then
    echo "工作模式不能合并现有个性化响应段；请使用独立工作 vault。" >&2
    return 1
  fi

  RENDERED_AGENTS="$(mktemp)"
  cp -p "$template" "$RENDERED_AGENTS"
  printf '\n' >> "$RENDERED_AGENTS"
  cat "$SCAFFOLD_DIR/$INSTALL_MODE.md" >> "$RENDERED_AGENTS"
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
  local layer scope directory
  local scopes=(工作)
  local directories=(.import_files 30_知识库)
  if [ "$INSTALL_MODE" = personal ]; then
    scopes+=(个人)
    directories+=(40_个人写作)
  fi
  if [ "$ROUTINE_TEMPLATES_MODE" = managed ]; then directories+=("$TEMPLATE_DIR"); fi
  for layer in 10_原始材料 20_每日记录 21_每周记录 22_每月记录 23_项目复盘; do
    directories+=("$layer")
    for scope in "${scopes[@]}"; do
      directories+=("$layer/$scope")
    done
  done
  for scope in "${scopes[@]}"; do directories+=("assets/$scope/figures"); done
  for directory in "${directories[@]}"; do
    mkdir -p "$VAULT_ROOT/$directory"
    touch "$VAULT_ROOT/$directory/.gitkeep"
  done
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
  local begin='// BEGIN SundayNoteAgent managed ignores'
  local end='// END SundayNoteAgent managed ignores'
  local line inside=0 blocks=0 rendered
  rendered="$(mktemp)"
  printf '%s\n' "$begin" > "$rendered"
  cat "$SCAFFOLD_DIR/.stignore" >> "$rendered"
  if [ "$INSTALL_MODE" = work ]; then cat "$SCAFFOLD_DIR/work.stignore" >> "$rendered"; fi
  printf '%s\n' "$end" >> "$rendered"
  if [ -f "$target" ]; then
    while IFS= read -r line || [ -n "$line" ]; do
      if [ "${line%$'\r'}" = "$begin" ]; then
        blocks=$((blocks + 1))
        if [ "$inside" -eq 1 ] || [ "$blocks" -gt 1 ]; then break; fi
        inside=1
      elif [ "${line%$'\r'}" = "$end" ]; then
        if [ "$inside" -eq 0 ]; then blocks=2; break; fi
        inside=0
      elif [ "$inside" -eq 0 ]; then
        # 接管旧安装追加的公共规则，其余用户规则保持顺序和内容。
        if ! grep -Fxq -- "${line%$'\r'}" "$SCAFFOLD_DIR/.stignore"; then
          printf '%s\n' "$line" >> "$rendered"
        fi
      fi
    done < "$target"
  fi
  if [ "$inside" -eq 1 ] || [ "$blocks" -gt 1 ]; then
    rm -f -- "$rendered"
    echo "无效的 .stignore 托管段：$target" >&2
    return 1
  fi
  copy_managed_file "$rendered" "$target"
  rm -f -- "$rendered"
}

paper_skill_path="$VAULT_ROOT/.agents/skills/paper-summarizer"
install_paper_summarizer=0
if [ "$WITH_PAPER_SUMMARIZER" -eq 1 ] || [ -e "$paper_skill_path" ] || [ -L "$paper_skill_path" ]; then
  install_paper_summarizer=1
fi

require_source_file "$SCAFFOLD_DIR/AGENTS.md"
require_source_file "$SCAFFOLD_DIR/$INSTALL_MODE.md"
if [ "$INSTALL_MODE" = work ]; then require_source_file "$SCAFFOLD_DIR/work-home.md"; fi
require_source_file "$SCAFFOLD_DIR/首页.md"
require_source_file "$SCAFFOLD_DIR/.gitignore"
require_source_file "$SCAFFOLD_DIR/.stignore"
if [ "$INSTALL_MODE" = work ]; then require_source_file "$SCAFFOLD_DIR/work.stignore"; fi
require_source_file "$SOURCE_ROOT/config/obsidian/calendar.json"
require_source_file "$SOURCE_ROOT/config/obsidian/daily-notes.json"
require_source_file "$SOURCE_ROOT/config/obsidian/quickadd.json"
require_source_file "$SCRIPT_DIR/configure_optional_integrations.py"
if [ "$ROUTINE_TEMPLATES_MODE" = managed ]; then
  for name in 每日记录 每周记录 每月记录; do require_source_file "$TEMPLATE_SOURCE/$name.md"; done
fi
require_source_dir "$SOURCE_ROOT/automation/quickadd"
for skill in "${CORE_SKILLS[@]}"; do require_source_dir "$SOURCE_ROOT/skills/$skill"; done
if [ "$INSTALL_MODE" = personal ]; then
  require_source_file "$SOURCE_ROOT/skills/sunday-note-context/assets/个人上下文.md"
fi
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
preflight_managed_file "$mode_file"

preflight_managed_file "$VAULT_ROOT/AGENTS.md"
preflight_local_file "$VAULT_ROOT/首页.md"
preflight_local_file "$VAULT_ROOT/.gitignore"
preflight_append_file "$VAULT_ROOT/.stignore"
if [ "$INSTALL_MODE" = personal ]; then preflight_local_file "$VAULT_ROOT/个人上下文.md"; fi
if [ "$ROUTINE_TEMPLATES_MODE" = managed ]; then
  preflight_container_dir "$VAULT_ROOT/$TEMPLATE_DIR"
  preflight_local_file "$VAULT_ROOT/$TEMPLATE_DIR/每日记录.md"
  preflight_managed_file "$VAULT_ROOT/$TEMPLATE_DIR/每周记录.md"
  preflight_managed_file "$VAULT_ROOT/$TEMPLATE_DIR/每月记录.md"
fi

for skill in "${CORE_SKILLS[@]}"; do preflight_managed_dir "$VAULT_ROOT/.agents/skills/$skill"; done
if [ "$install_paper_summarizer" -eq 1 ]; then
  preflight_managed_dir "$paper_skill_path"
fi
prepare_managed_agents

ensure_vault_dirs
copy_managed_file "$RENDERED_AGENTS" "$VAULT_ROOT/AGENTS.md"
if [ "$INSTALL_MODE" = work ]; then
  copy_if_missing "$SCAFFOLD_DIR/work-home.md" "$VAULT_ROOT/首页.md"
else
  copy_if_missing "$SCAFFOLD_DIR/首页.md" "$VAULT_ROOT/首页.md"
fi
copy_if_missing "$SCAFFOLD_DIR/.gitignore" "$VAULT_ROOT/.gitignore"
ensure_syncthing_ignores
if [ "$ROUTINE_TEMPLATES_MODE" = managed ]; then
  copy_if_missing "$TEMPLATE_SOURCE/每日记录.md" "$VAULT_ROOT/$TEMPLATE_DIR/每日记录.md"
  copy_managed_file "$TEMPLATE_SOURCE/每周记录.md" "$VAULT_ROOT/$TEMPLATE_DIR/每周记录.md"
  copy_managed_file "$TEMPLATE_SOURCE/每月记录.md" "$VAULT_ROOT/$TEMPLATE_DIR/每月记录.md"
else
  echo "已保留父 vault 的 Routine 模板与 Calendar 模板设置。"
fi
if [ "$INSTALL_MODE" = personal ]; then ensure_personal_context_file; fi

for skill in "${CORE_SKILLS[@]}"; do
  copy_managed_dir "$SOURCE_ROOT/skills/$skill" "$VAULT_ROOT/.agents/skills/$skill"
done
if [ "$install_paper_summarizer" -eq 1 ]; then
  copy_managed_dir "$SOURCE_ROOT/skills/paper-summarizer" "$paper_skill_path"
  rm -f \
    "$paper_skill_path/assets/embodied_ai_terminology.json" \
    "$paper_skill_path/scripts/write_summary_status.py" \
    "$paper_skill_path/assets/summary_template.json" \
    "$paper_skill_path/scripts/docling_parser.py" \
    "$paper_skill_path/scripts/prepare_paper_summary.py" \
    "$paper_skill_path/scripts/validate_summary.py"
fi

if [ -n "$OPTIONAL_CONFIG_PYTHON" ]; then
  optional_config_args=(--vault-root "$VAULT_ROOT" --mode "$INSTALL_MODE")
  if [ "$ROUTINE_TEMPLATES_MODE" = preserve ]; then
    optional_config_args+=(--preserve-templates)
  fi
  if ! "$OPTIONAL_CONFIG_PYTHON" "$SCRIPT_DIR/configure_optional_integrations.py" "${optional_config_args[@]}"; then
    echo "可选集成配置失败；核心安装已完成，Calendar/QuickAdd 未全部配置。" >&2
  fi
else
  echo "可选工作流未配置：Calendar Weekly 创建（未找到 python3 或 python；核心安装已完成）。"
  echo "可选工作流未配置：QuickAdd Routine 自动化（未找到 python3 或 python；核心安装已完成）。"
fi
echo "Installed or updated Sunday Note vault at: $VAULT_ROOT"
mkdir -p "$VAULT_ROOT/.sunday-note-agent"
printf '%s\n' "$INSTALL_MODE" > "$mode_file"
echo "安装模式：$INSTALL_MODE"
if [ -n "$MONITOR_MODE" ]; then configure_monitor; fi
if [ "$ROUTINE_TEMPLATES_MODE" = managed ]; then
  echo "Managed rules, skills, and Routine files were refreshed from: $PROJECT_DIR_NAME"
else
  echo "Managed rules and skills were refreshed from: $PROJECT_DIR_NAME"
fi
echo "Vault-local content and unmanaged configuration were preserved."
echo "安装期间应关闭 Obsidian；如果刚才正在运行，请退出后重新运行安装器，再启动 Obsidian。"
