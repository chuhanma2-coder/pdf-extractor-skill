# PDF 提取器：本地多 Agent Skill

从普通 PDF 与扫描件完整提取文本的本地工具。PDF 始终留在使用者电脑；运行包使用固定版本的 PDFium、RapidOCR、ONNX Runtime、RapidOCR 模型、Tesseract 与英文模型，不调用云端 OCR 或生成式 AI。

本仓库既包含通用的本地命令，也包含 `SKILL.md`。因此它可被支持本机 Shell 的 Agent 调用，而非只服务 Codex。各 Agent 的安装入口不同，详见 [多 Agent 使用说明](INTEGRATIONS_CN.md)。

## 最可靠的安装方式

```bash
git clone https://github.com/chuhanma2-coder/pdf-extractor-skill.git
cd pdf-extractor-skill
bash install.sh
```

安装完成后，让 Agent 执行：

```bash
python3 pdf-extractor-local/scripts/extract_pdf.py \
  --mode quality \
  --output-dir '/绝对路径/提取结果' \
  '/绝对路径/文件.pdf'
```

输出包括 `original.pdf`、`searchable.pdf`、`extracted.txt`、`extracted.md`、`extracted.json` 与 `manifest.json`。扫描件的置信度、双引擎差异和质量疑点保留在 JSON 中供人工核查。

## 平台与隐私

已验证 Apple Silicon Mac（M1、M2、M3、M4）与 Linux x86_64。Windows 与 Intel Mac 需要相应运行包完成构建和实测后才会发布。仅有网页聊天、不能访问本机文件和终端的 Agent 无法运行此工具。

只允许安装器从 GitHub 下载一次公开运行包；PDF 内容与结果不会上传到服务器。

## 固定运行包

Version `v0.3.0` must contain the GitHub Release asset `PDF-Extractor-macOS-arm64.zip` with SHA-256:

```text
213acbd6803492b645a0b65cb723aad13085275d0fe78ac805e495718eb7dcbf
```

安装器会校验该 SHA-256。Linux 构建使用与 macOS 包相同的 Tesseract `5.5.2`，以及相同 SHA-256 的 `eng.traineddata` 英文语言模型；模型清单见 [runtime-src/MODEL_MANIFEST.json](runtime-src/MODEL_MANIFEST.json)。不应改用系统安装的 Tesseract 或其他 OCR 模型，否则无法保证同等效果。不同操作系统的 OCR 原生二进制不能承诺逐字节完全相同，发布前必须分别完成样本文档验收。完整组件说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
