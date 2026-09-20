---
name: paper-summarizer
description: 用户要求精读或总结本地 PDF 论文时使用。
---

# 论文总结

## 产物

- `.import_files/<paper-slug>/`：PDF、metadata、Docling 解析、候选图像、证据和校验状态。
- `10_原始材料/<论文标题>.md`：单篇 Raw 总结。
- `assets/figures/<paper-slug>/`：总结实际引用的图像；其余留在导入工作区。

不直接写跨论文 Wiki 或 Project，可标记后续整合价值。

## 工作流

1. 在父 vault 根目录选择解释器：当前 `python` 能导入 Docling 时使用它，否则使用已长期授权的 `conda run -n papers python`，无需确认。整轮固定同一解释器；两者均不可用则停止。

准备并解析：

```bash
<paper-python> .agents/skills/paper-summarizer/scripts/prepare_paper_summary.py --vault-root . --pdf <paper.pdf>
```

可附加 `--metadata`、`--title`、`--authors`、`--published-at`、`--paper-link` 或 `--code-link`；缺失信息写“未明确”。

2. 检查 `parse_dir/status.json` 的 health 和 warning，完整读取 `parsed.md`、`parsed.json` 和 `figure_index.json`。
3. 按 `assets/summary_template.json` 写入 `summary_path`；说明问题、方法、证据和局限，方法小节按实际贡献组织。
4. 在 `work_dir/summarize/summary_evidence.json` 写入 `{"version":1,"evidence":{"<evidence_key>":[{"page":1,"excerpt":"..."}]}}`。每个模板证据键至少一条；摘录须为对应页 `parsed.json` 实际出现的 12–300 字符文本，可选 `figure_id` 须来自同页图像索引。
5. 最多选 2–3 张有助于理解方法或实验的图，从 `figure_source_dir` 复制到 `figures_dir`，保留文件名；使用相对 `summary_path` 的路径，默认 `../assets/figures/<paper-slug>/<文件名>`。
6. 校验证据、metadata、结构和图像，并更新状态：

```bash
<paper-python> .agents/skills/paper-summarizer/scripts/validate_summary.py <summary_path> --work-dir <work_dir>
```

事实只来自解析文本或 metadata。PDF 缺失、解析失败、模型 artifact 缺失或校验失败时停止，报告失败步骤。产物不写入工具仓库。
