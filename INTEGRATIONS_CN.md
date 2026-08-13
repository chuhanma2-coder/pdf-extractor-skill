# 多 Agent 使用说明

本仓库采用两层设计：`pdf-extractor-local/SKILL.md` 提供 Agent 工作流，GitHub Release 的运行包提供实际 OCR。Skill 文件不会改变模型、不会包含模型，也不会调用任何云端 OCR。

## 适用边界

- 已发布并验证：Apple Silicon macOS（M1、M2、M3、M4）。
- 需要 Agent 具备本机终端和本地文件读写权限，例如 Codex CLI/Desktop、Claude Code、Cursor、Trae、VS Code Agent 或其他可运行 Shell/Python 的本地 Agent。
- 仅网页对话、没有本机终端权限的 Agent，不能安装或运行本地 OCR；把 PDF 上传到这类网页产品也不等于它能调用本 Skill。
- Windows、Intel Mac 在对应运行包完成构建与实测前不支持。不要用不同 OCR 工具替代，否则质量无法保证一致。

## 所有支持本机工具调用的 Agent：通用安装

首次使用，在 Agent 能执行的终端中运行：

```bash
git clone https://github.com/chuhanma2-coder/pdf-extractor-skill.git
cd pdf-extractor-skill
bash install.sh
```

安装器只下载 GitHub Release 中固定版本的 Apple Silicon macOS 运行包，并先校验 SHA-256。运行包内已包含 PDFium、RapidOCR、ONNX Runtime、RapidOCR 模型、Tesseract、英文模型和导出逻辑；无需另装 Python OCR 库、Tesseract 或模型。

提取命令：

```bash
python3 pdf-extractor-local/scripts/extract_pdf.py \
  --mode quality \
  --output-dir '/绝对路径/提取结果' \
  '/绝对路径/文件.pdf'
```

## Codex

Codex 支持 `SKILL.md` 形式。可在对话中说：

```text
请安装这个 Skill：https://github.com/chuhanma2-coder/pdf-extractor-skill/tree/main/pdf-extractor-local
```

是否真的自动完成，取决于当前 Codex 会话是否允许安装 Skill 和执行本机命令。不能自动完成时，使用上面的通用终端安装；之后把 `pdf-extractor-local` 放入 Codex Skills 目录或让 Codex 读取该目录即可。

## Claude Code、Cursor、Trae 与其他本地 Agent

这些产品对“Skill”的目录、安装按钮和权限名称不同，因此不能承诺同一句“帮我安装”在所有产品中自动工作。通用可靠方式是先执行上述 `git clone + bash install.sh`，然后在对话中提供仓库或 `AGENTS.md`、`pdf-extractor-local/SKILL.md` 的路径，并要求 Agent 严格执行其中的本地提取命令。

## 结果与质量

每份 PDF 会生成：`original.pdf`、`searchable.pdf`、`extracted.txt`、`extracted.md`、`extracted.json`、`manifest.json`。普通 PDF 读取文本层；扫描件使用 300 DPI、RapidOCR 与 Tesseract 的原有双引擎逻辑。`extracted.json` 内的置信度与 `quality_issues` 必须保留。
