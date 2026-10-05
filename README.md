> **同学私有测试分支：** 请先阅读 [CLASSMATE_TEST.md](CLASSMATE_TEST.md)，使用 setup/start/stop-classmate-test.bat；网址为 http://127.0.0.1:8785。测试数据从随分支提供的固定公共材料生成，账号仅用于自己电脑上的合成库。

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

OCR 初始化前会检查 `official_models/` 中每个模型目录是否**可用**：`inference.yml`、`inference.json` 必须仍能解析为完整文档，参数文件必须存在且不小于模型应有的量级。PaddleX 只用「目录是否存在」判断缓存命中，所以更新或下载中断留下的空目录，以及复制到一半的**非零但被截断**文件，都会让 OCR 一直失败；应用只删除这类可重新下载的模型目录，让 PaddleOCR 重新获取它。

删除前还有几道闸门，确保不会误删：只处理 PaddleX 官方模型清单里的目录名、内容确实是模型文件、不是符号链接／目录联接、最近一段时间内没有被写入（PaddleX 会直接写进已存在的模型目录，新写入意味着可能仍在下载）、并且能拿到 PaddleX 自己的跨进程下载锁。**清单本身读不到时（PaddleX 未安装、导入失败或清单为空）不做任何猜测：一律跳过清理并记录原因**，因为「少清理一次」只是多一次可人工处理的 OCR 启动失败，而「删错目录」会真的丢数据。删除时先把目录原子改名，再删除改名后的路径，因此正在被其它进程使用的缓存不会被抽走。检查与清理**只作用于 `ocr-models/official_models/`**，同级的 `locks/`、`temp/`、`func_ret/` 不在范围内；词库、上传图片和本机配置都不会被触碰。跳过某个目录的原因会记在返回值与日志里。

## 安装到手机（PWA 外壳）

前端构建产物包含 `manifest.webmanifest`、专用的 `192/512/180` 安装图标与一个 Service Worker（仅 production 构建注册）。Service Worker 是**离线应用壳，不是离线数据副本**：

- 只缓存 `/assets/`、`/icons/`、`manifest.webmanifest` 与应用壳；
- **`/api` 与 `/api/**`、所有非 GET 请求、跨源请求一律直连网络**，所以切换账号后不会重放出上一个账号的私有数据；
- 不做后台同步，也不做离线写队列（非目标）。

要安装：先 `npm run build`，启动服务后从手机访问同一地址并选择「添加到主屏幕」。注意当前服务只监听回环地址，手机访问还需要自行调整监听地址或使用反向代理。

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
