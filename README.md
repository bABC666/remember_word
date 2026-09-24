# 拾词

拾词是一款本地英语词汇学习应用。它将单词书照片导入、人工校对、主动回忆、阅读复现和完整历史保存在同一个 SQLite 数据库中。

## 日常启动（Windows）

双击桌面的“拾词”快捷方式，或双击根目录的 `start-vocab.bat`。脚本会：

1. 首次运行时创建 Python 环境，并安装后端、PaddleOCR CPU 与前端依赖；
2. 安装或构建 React 前端；
3. 执行 Alembic migration；
4. 启动 FastAPI，并由 FastAPI 托管 `frontend/dist`；
5. 自动打开 `http://127.0.0.1:8000`。

停止后台服务时双击 `stop-vocab.bat`。

桌面快捷方式使用稳定入口 `start-vocab.bat`，并在每次启动时自动刷新目标路径、工作目录和项目图标。需要手动重新安装或更新时，运行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\install-shortcut.ps1
```

图标源文件保存在 `assets/shici-app.png`，Windows 多尺寸图标保存在 `assets/shici-app.ico`。

## 首次配置

复制 `.env.example` 为 `.env`，填写 DeepSeek API Key：

```dotenv
DEEPSEEK_API_KEY=your-key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-flash
```

也可以在设置页面保存 Key。本地配置保存在 `data/config/settings.json`，不会被 Git 跟踪；设置接口只返回掩码。

## PaddleOCR

PaddleOCR provider 已实现为真实适配器，不包含 mock。CPU 安装方式：

```powershell
backend\.venv\Scripts\python.exe -m pip install paddlepaddle==3.3.0 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
backend\.venv\Scripts\python.exe -m pip install -e "backend[ocr]"
```

首次 OCR 会下载官方模型，所需时间取决于网络。如果主后端环境出现平台依赖冲突，可以使用 Python 3.11/3.12 创建独立 OCR 环境；`OCRProvider.extract(path) -> OCRDocument` 接口不需要改变。

在包含中文用户名或中文项目路径的 Windows 上，Paddle Inference 可能无法直接读取模型路径。应用会把模型实体保存在 `data/ocr-models/`，并在系统临时目录自动创建一个仅用于兼容底层推理库的 ASCII 目录联接；模型数据仍然集中在项目 data 目录。

OCR 首次初始化会检查 `official_models/` 中已有模型的 `inference.yml`、`inference.json` 与参数文件。若更新或下载中断留下不完整模型目录，应用只删除该可再下载的目录并让 PaddleOCR 重新获取它；不会触碰词库、上传图片或配置。

当前 Windows CPU 组合会关闭 PaddleX 默认 oneDNN 路径，绕过 Paddle 3.3 PIR 执行器对 OCR 检测模型中数组属性的未实现转换；实际 OCR 仍由 PaddleOCR/PaddlePaddle 完成。

## 开发方式

后端：

```powershell
backend\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --reload
```

前端：

```powershell
cd frontend
npm run dev
```

Vite 会把 `/api` 代理到 `127.0.0.1:8000`。

## 测试与构建

```powershell
backend\.venv\Scripts\python.exe -m pytest backend\tests -q
backend\.venv\Scripts\python.exe -m ruff check backend
cd frontend
npm test
npm run typecheck
npm run lint
npm run build
```

## 目录

- `backend/app/api/`：FastAPI 路由。
- `backend/app/models.py`：Source、Learning、History 的 SQLAlchemy 数据模型。
- `backend/app/services/ocr/`：可替换 OCR provider。
- `backend/app/services/ai/`：可替换 AI provider。
- `backend/app/prompts/`：DeepSeek prompts。
- `backend/alembic/`：数据库 migration。
- `frontend/src/pages/`：六个 V1 页面。
- `data/vocab.db`：唯一事实数据库。
- `data/uploads/`：原始图片文件。
- `data/backups/`：自动与手动 SQLite 备份。
- `data/config/`：本机敏感配置。

## 数据原则

AI 不会更新 `source_raw`、`source_meanings` 或 OCR 原始结果。未经人工确认的 `import_candidate` 不会进入 `word`。每次复习写入独立 `review_event`；每次真实文章暴露写入 `article_word_exposure`。

## 项目交接

需要把项目交给另一个开发者或 AI 时，先阅读 [`docs/PROJECT_HANDOFF.md`](docs/PROJECT_HANDOFF.md)。该文档汇总当前版本、架构边界、数据库状态、已完成能力、测试证据、已知限制和后续建议。
