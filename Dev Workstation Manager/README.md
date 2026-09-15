# Dev Tool Manager

Double-click **Launch Dev Tool Manager.cmd** to open the browser installer. Tick the tools you want, then press **Install selected**.

Click **Install core stack** for Java 17, Python 3.12 (including pip), Node.js 22, and Maven, or click **Install full workstation** for every supported tool:

- Docker
- Git
- Java 17
- Node.js 22
- Maven
- Python 3.12
- kubectl
- Helm
- Terraform
- AWS CLI
- Azure CLI
- Go and pip
- IntelliJ IDEA Community, PyCharm Community, and Visual Studio Community
- HeidiSQL and MySQL Workbench
- Kafka Assistant
- Ollama, LM Studio, and Open WebUI for local LLM use
- Codex CLI, Claude Code, Gemini CLI, Aider, and Hugging Face CLI

The app uses Windows Package Manager (`winget`) and keeps the browser page open while installs run. Maven is downloaded from the official Apache Maven distribution because its winget package is no longer available. Windows may ask for permission when an installer needs it. If winget is unavailable, install or update **App Installer** from Microsoft Store and open the tool again.
