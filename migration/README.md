# 迁移工具

本目录保存可复用的知识库迁移辅助工具，用于把外部知识源导入 vault。

## 飞书文档导出

`export_feishu_docx.py` 将飞书云盘或 Wiki 文档导出为 `.docx` 文件。

输入：

- 飞书凭据：`FEISHU_APP_ID` 和 `FEISHU_APP_SECRET`，或 `FEISHU_ACCESS_TOKEN`。
- 飞书 folder、wiki、doc 或 docx URL：通过 `--url` 或 `FEISHU_ROOT_URL` 传入。
- 可选输出目录：通过 `--output-dir` 或 `FEISHU_EXPORT_OUT` 传入。

默认输出到导入工作区：

```text
.import_files
```

导出前先使用 `--dry-run` 检查目标。脚本会在输出目录写入 `manifest.jsonl`，但不会把凭据写入磁盘。转换后的 Markdown 来源材料再进入 `工作资料/`。
