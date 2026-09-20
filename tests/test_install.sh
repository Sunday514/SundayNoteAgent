#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT

fail() {
  echo "install fixture failed: $*" >&2
  exit 1
}

assert_file_contains() {
  grep -Fq -- "$2" "$1" || fail "$1 does not contain: $2"
}

assert_output_contains() {
  grep -Fq -- "$2" <<< "$1" || fail "installer output does not contain: $2"
}

assert_same_file() {
  cmp -s -- "$1" "$2" || fail "files differ: $1 -> $2"
}

assert_source_tree_exported() {
  local source_dir="$1"
  local target_dir="$2"
  local source_file
  local relative_path

  while IFS= read -r -d '' source_file; do
    relative_path="${source_file#"$source_dir/"}"
    test -f "$target_dir/$relative_path" || fail "missing exported file: $target_dir/$relative_path"
    assert_same_file "$source_file" "$target_dir/$relative_path"
  done < <(find "$source_dir" -type f -print0)
}

make_command_path() {
  local target="$1"
  shift
  local command_name

  mkdir -p "$target"
  for command_name in "$@"; do
    ln -s "$(command -v "$command_name")" "$target/$command_name"
  done
}

shell_only_path="$TMP_ROOT/shell-only-bin"
make_command_path "$shell_only_path" bash cat chmod cp dirname grep mkdir mktemp rm tail touch

vault="$TMP_ROOT/vault"
mkdir -p "$vault/SundayNoteAgent"

install_output="$(PATH="$shell_only_path" "$shell_only_path/bash" "$ROOT/install/install.sh" --vault-root "$vault")"

test -f "$vault/AGENTS.md" || fail "new vault is missing AGENTS.md"
test -f "$vault/个人上下文.md" || fail "new vault is missing root personal context"
test -f "$vault/个人模板/每日记录.md" || fail "new vault is missing Daily template"
test ! -e "$vault/.agents/skills/paper-summarizer" || fail "optional skill was installed without opt-in"
test ! -e "$vault/.obsidian/community-plugins.json" || fail "installer created a community plugin baseline"
test ! -e "$vault/.obsidian/plugins/calendar/data.json" || fail "installer configured missing Calendar"
test ! -e "$vault/.obsidian/plugins/quickadd/data.json" || fail "installer configured missing QuickAdd"
assert_output_contains "$install_output" "可选工作流未配置：Calendar Weekly 创建"
assert_output_contains "$install_output" "可选工作流未配置：QuickAdd Routine 自动化"
assert_output_contains "$install_output" "安装期间应关闭 Obsidian"
for required_dir in \
  .import_files \
  10_原始材料 \
  20_每日记录 \
  21_每周记录 \
  22_每月记录 \
  23_项目复盘 \
  30_知识库 \
  40_个人写作 \
  个人模板 \
  assets/figures; do
  test -d "$vault/$required_dir" || fail "new vault is missing fixed directory: $required_dir"
done
assert_file_contains "$vault/.stignore" "/SundayNoteAgent"
assert_file_contains "$vault/.stignore" "/.import_files"
assert_same_file "$ROOT/install/scaffold/AGENTS.md" "$vault/AGENTS.md"
assert_same_file "$ROOT/skills/sunday-note-context/assets/个人上下文.md" "$vault/个人上下文.md"

assert_same_file "$ROOT/templates/每周记录.md" "$vault/个人模板/每周记录.md"
assert_file_contains "$vault/个人模板/每周记录.md" "value-week={{title}}"
assert_file_contains "$vault/个人模板/每周记录.md" "- [ ] 本周计划事项"
assert_same_file "$ROOT/templates/每月记录.md" "$vault/个人模板/每月记录.md"
assert_file_contains "$vault/个人模板/每月记录.md" "value-month={month}"
assert_file_contains "$vault/个人模板/每月记录.md" "- [ ] 本月计划事项"
assert_source_tree_exported "$ROOT/skills/sunday-note-ingest" "$vault/.agents/skills/sunday-note-ingest"
assert_source_tree_exported "$ROOT/skills/sunday-note-lint" "$vault/.agents/skills/sunday-note-lint"
assert_source_tree_exported "$ROOT/skills/sunday-note-query" "$vault/.agents/skills/sunday-note-query"
assert_source_tree_exported "$ROOT/skills/sunday-note-context" "$vault/.agents/skills/sunday-note-context"

integrated="$TMP_ROOT/integrated"
mkdir -p \
  "$integrated/SundayNoteAgent/automation/quickadd" \
  "$integrated/.obsidian/plugins/calendar" \
  "$integrated/.obsidian/plugins/quickadd"
cp "$ROOT/automation/quickadd/rollup.js" "$integrated/SundayNoteAgent/automation/quickadd/rollup.js"
cat > "$integrated/.obsidian/community-plugins.json" <<'EOF'
[
  "calendar",
  "quickadd",
  "unrelated-plugin"
]
EOF
printf '%s\n' '{"id":"calendar"}' > "$integrated/.obsidian/plugins/calendar/manifest.json"
printf '%s\n' '{"id":"quickadd"}' > "$integrated/.obsidian/plugins/quickadd/manifest.json"
cat > "$integrated/.obsidian/plugins/calendar/data.json" <<'EOF'
{
  "weekStart": "sunday",
  "showWeeklyNote": false,
  "weeklyNoteFormat": "old-format",
  "weeklyNoteTemplate": "自定义模板/旧模板.md",
  "weeklyNoteFolder": "旧周记"
}
EOF
cat > "$integrated/.obsidian/plugins/quickadd/data.json" <<'EOF'
{
  "choices": [
    {
      "id": "custom-choice",
      "name": "用户 choice",
      "type": "Template"
    },
    {
      "id": "sunday-note-rollup-week",
      "name": "过期周统计",
      "type": "Macro"
    },
    {
      "id": "legacy-month-choice",
      "name": "刷新每月统计",
      "type": "Macro"
    }
  ],
  "localSetting": "preserve-me"
}
EOF

cp "$integrated/.obsidian/community-plugins.json" "$TMP_ROOT/community-plugins.before.json"
python3_only_path="$TMP_ROOT/python3-only-bin"
make_command_path "$python3_only_path" bash cat chmod cp dirname grep mkdir mktemp python3 rm tail touch
test ! -e "$python3_only_path/python" || fail "python3-only fixture unexpectedly exposes python"
integration_output="$(PATH="$python3_only_path" "$python3_only_path/bash" "$ROOT/install/install.sh" --vault-root "$integrated")"
assert_output_contains "$integration_output" "已配置可选工作流：Calendar Weekly 创建。"
assert_output_contains "$integration_output" "已配置可选工作流：QuickAdd Routine 自动化。"
assert_same_file "$TMP_ROOT/community-plugins.before.json" "$integrated/.obsidian/community-plugins.json"

calendar_data="$integrated/.obsidian/plugins/calendar/data.json"
assert_file_contains "$calendar_data" '"weekStart": "sunday"'
assert_file_contains "$calendar_data" '"showWeeklyNote": true'
assert_file_contains "$calendar_data" '"weeklyNoteFormat": "gggg-[W]ww"'
assert_file_contains "$calendar_data" '"weeklyNoteTemplate": "个人模板/每周记录.md"'
assert_file_contains "$calendar_data" '"weeklyNoteFolder": "21_每周记录"'

quickadd_data="$integrated/.obsidian/plugins/quickadd/data.json"
assert_file_contains "$quickadd_data" '"id": "custom-choice"'
assert_file_contains "$quickadd_data" '"localSetting": "preserve-me"'
assert_file_contains "$quickadd_data" '"name": "统计本周打卡"'
assert_file_contains "$quickadd_data" '"name": "刷新每月统计"'
assert_file_contains "$quickadd_data" '"path": "SundayNoteAgent/automation/quickadd/rollup.js"'
assert_same_file "$ROOT/automation/quickadd/rollup.js" "$integrated/SundayNoteAgent/automation/quickadd/rollup.js"
if grep -Fq -- '过期' "$quickadd_data"; then
  fail "installer preserved a stale managed QuickAdd choice"
fi
python -c '
import json
import sys

data = json.load(open(sys.argv[1], encoding="utf-8"))
ids = [choice.get("id") for choice in data["choices"]]
names = [choice.get("name") for choice in data["choices"]]
expected = {
    "sunday-note-rollup-week",
    "sunday-note-rollup-month-pack",
}
assert all(ids.count(choice_id) == 1 for choice_id in expected)
assert "legacy-month-choice" not in ids
assert names.count("统计本周打卡") == 1
assert names.count("刷新每月统计") == 1
assert "custom-choice" in ids
' "$quickadd_data" || fail "managed choices were duplicated or a parent choice was removed"
test "$(grep -Fc -- '"path": "SundayNoteAgent/automation/quickadd/rollup.js"' "$quickadd_data")" -eq 2 || fail "QuickAdd choices do not use the visible project script"

cp "$calendar_data" "$TMP_ROOT/calendar.after-first.json"
cp "$quickadd_data" "$TMP_ROOT/quickadd.after-first.json"
bash "$ROOT/install/install.sh" --vault-root "$integrated" >/dev/null
assert_same_file "$TMP_ROOT/calendar.after-first.json" "$calendar_data"
assert_same_file "$TMP_ROOT/quickadd.after-first.json" "$quickadd_data"
assert_same_file "$TMP_ROOT/community-plugins.before.json" "$integrated/.obsidian/community-plugins.json"

preserved="$TMP_ROOT/preserved"
mkdir -p \
  "$preserved/SundayNoteAgent" \
  "$preserved/个人模板" \
  "$preserved/.obsidian/plugins/calendar" \
  "$preserved/.obsidian/plugins/quickadd"
printf '%s\n' 'custom daily' > "$preserved/个人模板/日记录模板.md"
printf '%s\n' 'custom weekly' > "$preserved/个人模板/周记录模板.md"
printf '%s\n' 'custom monthly' > "$preserved/个人模板/月记录模板.md"
cat > "$preserved/.obsidian/community-plugins.json" <<'EOF'
[
  "calendar",
  "quickadd"
]
EOF
printf '%s\n' '{"id":"calendar"}' > "$preserved/.obsidian/plugins/calendar/manifest.json"
printf '%s\n' '{"id":"quickadd"}' > "$preserved/.obsidian/plugins/quickadd/manifest.json"
cat > "$preserved/.obsidian/plugins/calendar/data.json" <<'EOF'
{
  "showWeeklyNote": true,
  "weeklyNoteFormat": "YYYY-[W]WW",
  "weeklyNoteTemplate": "个人模板/周记录模板.md",
  "weeklyNoteFolder": "21_每周记录"
}
EOF
printf '%s\n' '{"choices":[]}' > "$preserved/.obsidian/plugins/quickadd/data.json"
cp -R "$preserved/个人模板" "$TMP_ROOT/preserved-templates.before"
cp "$preserved/.obsidian/plugins/calendar/data.json" "$TMP_ROOT/preserved-calendar.before.json"

preserve_output="$(bash "$ROOT/install/install.sh" --vault-root "$preserved" --routine-templates preserve)"
assert_output_contains "$preserve_output" "已保留父 vault 的 Routine 模板与 Calendar 模板设置。"
assert_same_file "$TMP_ROOT/preserved-templates.before/日记录模板.md" "$preserved/个人模板/日记录模板.md"
assert_same_file "$TMP_ROOT/preserved-templates.before/周记录模板.md" "$preserved/个人模板/周记录模板.md"
assert_same_file "$TMP_ROOT/preserved-templates.before/月记录模板.md" "$preserved/个人模板/月记录模板.md"
test ! -e "$preserved/个人模板/每日记录.md" || fail "preserve mode created a Daily template"
test ! -e "$preserved/个人模板/每周记录.md" || fail "preserve mode created a Weekly template"
test ! -e "$preserved/个人模板/每月记录.md" || fail "preserve mode created a monthly template"
assert_same_file "$TMP_ROOT/preserved-calendar.before.json" "$preserved/.obsidian/plugins/calendar/data.json"
assert_file_contains "$preserved/.obsidian/plugins/quickadd/data.json" '"name": "统计本周打卡"'

symlink_vault="$TMP_ROOT/symlink-vault"
outside_calendar="$TMP_ROOT/outside-calendar.json"
mkdir -p "$symlink_vault/SundayNoteAgent" "$symlink_vault/.obsidian/plugins/calendar"
printf '%s\n' '["calendar"]' > "$symlink_vault/.obsidian/community-plugins.json"
printf '%s\n' '{"id":"calendar"}' > "$symlink_vault/.obsidian/plugins/calendar/manifest.json"
printf '%s\n' '{"outside":"unchanged"}' > "$outside_calendar"
ln -s "$outside_calendar" "$symlink_vault/.obsidian/plugins/calendar/data.json"
symlink_output="$(bash "$ROOT/install/install.sh" --vault-root "$symlink_vault" 2>&1)"
assert_output_contains "$symlink_output" "可选集成配置失败；核心安装已完成"
test -f "$symlink_vault/AGENTS.md" || fail "optional integration failure blocked core installation"
assert_output_contains "$symlink_output" "拒绝写入符号链接"
assert_file_contains "$outside_calendar" '"outside":"unchanged"'

cat > "$vault/30_知识库/集成测试.md" <<'EOF'
---
last_updated: 2026-07-13
update_count: 1
last_queried: ""
query_count: 0
sources: ["[[10_原始材料/集成来源]]"]
topic: "集成测试"
keywords: ["唯一集成词"]
---

# 集成测试

唯一集成词。

[[10_原始材料/集成来源]]
[[20_每日记录/2026-07-13]]
EOF
cat > "$vault/30_知识库/索引.md" <<'EOF'
---
last_updated: 2026-07-13
update_count: 1
last_queried: ""
query_count: 0
sources: []
topic: "索引"
keywords: ["索引"]
---

[[集成测试]]
EOF
printf '%s\n' "# 集成来源" > "$vault/10_原始材料/集成来源.md"
printf '%s\n' "唯一集成词只应作为直接来源读取。" > "$vault/20_每日记录/2026-07-13.md"

query_output="$(python "$vault/.agents/skills/sunday-note-query/scripts/query_search.py" \
  "唯一集成词" --vault-root "$vault")"
assert_file_contains <(printf '%s' "$query_output") "30_知识库/集成测试.md"
if grep -Fq "20_每日记录" <<< "$query_output"; then
  fail "installed Query searched Routine directly"
fi

python "$vault/.agents/skills/sunday-note-query/scripts/update_query_header.py" \
  "30_知识库/集成测试.md" --vault-root "$vault" --date 2026-07-13 >/dev/null
assert_file_contains "$vault/30_知识库/集成测试.md" "query_count: 1"
assert_file_contains "$vault/30_知识库/集成测试.md" "唯一集成词。"

lint_output="$(python "$vault/.agents/skills/sunday-note-lint/scripts/lint_headers.py" \
  --root "$vault" --scope "30_知识库/集成测试.md" --format json)"
assert_file_contains <(printf '%s' "$lint_output") '"issue_files": 0'

audit_output="$(python "$vault/.agents/skills/sunday-note-lint/scripts/audit_reachability.py" \
  --root "$vault" \
  --wiki-entry "30_知识库/索引.md" \
  --wiki-scope "30_知识库/索引.md" \
  --wiki-scope "30_知识库/集成测试.md" \
  --raw-scope "10_原始材料/集成来源.md" \
  --routine-scope "20_每日记录/2026-07-13.md" \
  --format json)"
assert_file_contains <(printf '%s' "$audit_output") '"wiki_unreachable": []'
assert_file_contains <(printf '%s' "$audit_output") '"raw_unlinked": []'

printf '%s\n' "local rollup config" > "$vault/.sunday-note-agent/config/quickadd-rollups.json"
printf '%s\n' "local template" > "$vault/个人模板/每日记录.md"
printf '%s\n' "stale managed rule" > "$vault/AGENTS.md"
printf '%s\n' "personal context sentinel" >> "$vault/个人上下文.md"
printf '%s\n' "stale managed script" > "$vault/.agents/skills/sunday-note-query/scripts/query_search.py"
printf '%s\n' "extra skill file" > "$vault/.agents/skills/sunday-note-query/local.md"
printf '%s\n' "local ignore" > "$vault/.stignore"

snapshot="$TMP_ROOT/local-content"
mkdir -p "$snapshot"
cp "$vault/.sunday-note-agent/config/quickadd-rollups.json" "$snapshot/config"
cp "$vault/个人模板/每日记录.md" "$snapshot/template"
cp "$vault/10_原始材料/集成来源.md" "$snapshot/raw"
cp "$vault/20_每日记录/2026-07-13.md" "$snapshot/routine"
cp "$vault/30_知识库/集成测试.md" "$snapshot/wiki"
cp "$vault/个人上下文.md" "$snapshot/personal-context"

assert_local_content_unchanged() {
  assert_same_file "$snapshot/config" "$vault/.sunday-note-agent/config/quickadd-rollups.json"
  assert_same_file "$snapshot/template" "$vault/个人模板/每日记录.md"
  assert_same_file "$snapshot/raw" "$vault/10_原始材料/集成来源.md"
  assert_same_file "$snapshot/routine" "$vault/20_每日记录/2026-07-13.md"
  assert_same_file "$snapshot/wiki" "$vault/30_知识库/集成测试.md"
  assert_same_file "$snapshot/personal-context" "$vault/个人上下文.md"
}

bash "$ROOT/install/install.sh" --vault-root "$vault" --with-paper-summarizer >/dev/null

assert_file_contains "$vault/AGENTS.md" "Agent"
assert_file_contains "$vault/.sunday-note-agent/config/quickadd-rollups.json" "local rollup config"
assert_file_contains "$vault/个人模板/每日记录.md" "local template"
assert_file_contains "$vault/.agents/skills/sunday-note-query/local.md" "extra skill file"
assert_file_contains "$vault/.stignore" "local ignore"
assert_file_contains "$vault/.stignore" "/SundayNoteAgent"
assert_file_contains "$vault/.stignore" "/.import_files"
test -f "$vault/.agents/skills/paper-summarizer/SKILL.md" || fail "optional skill was not installed"
assert_local_content_unchanged
assert_source_tree_exported "$ROOT/skills/sunday-note-ingest" "$vault/.agents/skills/sunday-note-ingest"
assert_source_tree_exported "$ROOT/skills/sunday-note-lint" "$vault/.agents/skills/sunday-note-lint"
assert_source_tree_exported "$ROOT/skills/sunday-note-query" "$vault/.agents/skills/sunday-note-query"
assert_source_tree_exported "$ROOT/skills/sunday-note-context" "$vault/.agents/skills/sunday-note-context"
assert_source_tree_exported "$ROOT/skills/paper-summarizer" "$vault/.agents/skills/paper-summarizer"
assert_same_file "$ROOT/templates/每周记录.md" "$vault/个人模板/每周记录.md"
assert_same_file "$ROOT/templates/每月记录.md" "$vault/个人模板/每月记录.md"

printf '%s\n' \
  '' \
  '## 个性化响应' \
  'prompt paragraph sentinel' \
  '' \
  '仅在当前任务需要进一步了解个人长期项目、取舍或表达偏好时，再读取：' \
  '- [[个人上下文]]' >> "$vault/AGENTS.md"
bash "$ROOT/install/install.sh" --vault-root "$vault" >/dev/null
assert_file_contains "$vault/AGENTS.md" "prompt paragraph sentinel"
test "$(grep -Fxc -- "prompt paragraph sentinel" "$vault/AGENTS.md")" -eq 1 || fail "personal prompt sentinel was duplicated"
test "$(grep -Fxc -- "## 个性化响应" "$vault/AGENTS.md")" -eq 1 || fail "personal prompt heading was duplicated"
assert_local_content_unchanged

crlf_vault="$TMP_ROOT/crlf-vault"
mkdir -p "$crlf_vault/SundayNoteAgent"
printf '%s\r\n' '## 个性化响应' '' 'CRLF personal sentinel' '- [[个人上下文]]' > "$TMP_ROOT/personal-crlf"
cp "$TMP_ROOT/personal-crlf" "$crlf_vault/AGENTS.md"
for attempt in 1 2; do
  bash "$ROOT/install/install.sh" --vault-root "$crlf_vault" >/dev/null
  python - "$crlf_vault/AGENTS.md" "$TMP_ROOT/personal-crlf" <<'PY'
import sys
from pathlib import Path
actual = Path(sys.argv[1]).read_bytes()
personal = Path(sys.argv[2]).read_bytes()
assert actual.endswith(personal), "CRLF personal section changed"
assert actual.count(personal) == 1, "CRLF personal section duplicated"
PY
done
printf '%s\n' '## 个性化响应' >> "$crlf_vault/AGENTS.md"
cp "$crlf_vault/AGENTS.md" "$TMP_ROOT/duplicate-personal"
if bash "$ROOT/install/install.sh" --vault-root "$crlf_vault" >"$TMP_ROOT/duplicate.out" 2>&1; then
  fail "mixed-newline duplicate personal headings did not stop installation"
fi
assert_same_file "$TMP_ROOT/duplicate-personal" "$crlf_vault/AGENTS.md"

printf '%s\n' "stale optional skill" > "$vault/.agents/skills/paper-summarizer/SKILL.md"
printf '%s\n' "obsolete status script" > "$vault/.agents/skills/paper-summarizer/scripts/write_summary_status.py"
printf '%s\n' '{}' > "$vault/.agents/skills/paper-summarizer/assets/embodied_ai_terminology.json"
printf '%s\n' "local paper extension" > "$vault/.agents/skills/paper-summarizer/local.md"
bash "$ROOT/install/install.sh" --vault-root "$vault" >/dev/null
if grep -Fq "stale optional skill" "$vault/.agents/skills/paper-summarizer/SKILL.md"; then
  fail "enabled optional skill was not refreshed"
fi
test ! -e "$vault/.agents/skills/paper-summarizer/scripts/write_summary_status.py" || fail "obsolete paper status script was not removed"
test ! -e "$vault/.agents/skills/paper-summarizer/assets/embodied_ai_terminology.json" || fail "obsolete paper terminology asset was not removed"
assert_file_contains "$vault/.agents/skills/paper-summarizer/local.md" "local paper extension"
assert_local_content_unchanged
assert_source_tree_exported "$ROOT/skills/paper-summarizer" "$vault/.agents/skills/paper-summarizer"
assert_same_file "$ROOT/templates/每周记录.md" "$vault/个人模板/每周记录.md"
assert_same_file "$ROOT/templates/每月记录.md" "$vault/个人模板/每月记录.md"
test "$(grep -Fxc -- "/SundayNoteAgent" "$vault/.stignore")" -eq 1 || fail "Syncthing ignore rule was duplicated"
test "$(grep -Fxc -- "/.import_files" "$vault/.stignore")" -eq 1 || fail "Syncthing import ignore rule was duplicated"

conflict="$TMP_ROOT/conflict"
mkdir -p "$conflict/SundayNoteAgent" "$conflict/.sunday-note-agent"
printf '%s\n' "do not replace" > "$conflict/.sunday-note-agent/config"
if bash "$ROOT/install/install.sh" --vault-root "$conflict" >"$TMP_ROOT/conflict.out" 2>&1; then
  fail "real-file directory conflict did not stop installation"
fi
assert_file_contains "$TMP_ROOT/conflict.out" "$conflict/.sunday-note-agent/config"
assert_file_contains "$conflict/.sunday-note-agent/config" "do not replace"

stignore_conflict="$TMP_ROOT/stignore-conflict"
mkdir -p "$stignore_conflict/SundayNoteAgent" "$stignore_conflict/.stignore"
if bash "$ROOT/install/install.sh" --vault-root "$stignore_conflict" >"$TMP_ROOT/stignore-conflict.out" 2>&1; then
  fail "directory at .stignore did not stop installation"
fi
assert_file_contains "$TMP_ROOT/stignore-conflict.out" "$stignore_conflict/.stignore"

echo "install fixture passed"
