# Install With An Agent

Paste this single instruction into an Agent that can access a terminal and the internet:

```text
请安装并启用这个本地 PDF 提取 Skill：https://github.com/chuhanma2-coder/pdf-extractor-skill 。请克隆仓库，运行仓库根目录的 bash install.sh，执行运行包 health 验证；不要上传 PDF、不要改用其他 OCR 模型。完成后告诉我支持的平台、安装路径和验证结果。
```

The Agent must be allowed to clone a public GitHub repository and execute shell commands. A chat-only Agent, or one whose sandbox blocks downloads or command execution, cannot install any local Skill from a link alone.

On supported systems the runtime is downloaded once and used locally afterwards:

- Apple Silicon macOS
- Linux x86_64, including WorkBuddy's Ubuntu sandbox

No API key, VPN, Python environment, system Tesseract, or cloud OCR account is required.
