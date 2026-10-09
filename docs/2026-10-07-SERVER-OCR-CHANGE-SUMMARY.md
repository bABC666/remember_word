# 服务器部署与 OCR 适配改动总结

记录日期：2026-10-07。范围：2026-09-30 的 Linux 部署材料，以及本对话在
2026-10-01 完成的 2 核 / 2GB ECS OCR 适配与增量交付。

**确实增加了代码。** 应用原来已经有 PaddleOCR 适配器，可以在服务器进程中运行；
这次修改现有适配器以支持小内存 CPU 实例，并新增服务器自检脚本、测试和部署说明。
没有另建一个 OCR 微服务，也没有重写整套应用。

最初的 GitHub 提交仅归档总结，未包含实际代码。负责人核对后要求落实实现补交，
截至本次更新，OCR 适配器、回归测试和下述 7 个部署文件已补交到 GitHub `main`。
已核对远端适配器包含 `VOCAB_OCR_PROFILE`，代码和测试正文与本地已验证版本一致。
本文同步记录实现补交范围。服务器安装、重启和上线验收仍是独立步骤，本对话
没有服务器上线成功的实测证据。

远端核对入口：[OCR 代码](../backend/app/services/ocr/paddle.py)、
[回归测试](../backend/tests/test_ocr_optimization.py)、[部署目录](../deploy/)、
[公网 2GB ECS 部署说明](../deploy/OCR-2GB-WORKBENCH.md)。

补交文件清单（相对于仓库根目录）：

```text
backend/app/services/ocr/paddle.py
backend/tests/test_ocr_optimization.py
deploy/OCR-2GB-WORKBENCH.md
deploy/ocr-2gb.env.example
deploy/OCR.md
deploy/README.md
deploy/shici.env.example
deploy/shici.service
deploy/verify-ocr.py
docs/2026-10-07-SERVER-OCR-CHANGE-SUMMARY.md
```

## 1. 服务器环境与当时的问题

用户提供的环境为 Alibaba Cloud Linux 3.2104 LTS 64 位、阿里云 ECS
`ecs.e-c1m1.large`、2 核 CPU、2GB 内存、40GB 系统盘。用户已更正：拾词已经在
公网运行，应以公网运行状态为准，不沿用早期“仅通过 SSH 通道访问”的描述。

部署布局：

| 项目 | 路径 / 名称 |
| --- | --- |
| 前端构建产物及后端代码 | `/opt/shici/current` |
| 服务 Python 环境 | `/opt/shici/current/.venv` |
| systemd 服务 | `shici.service` |
| SQLite 数据库 | `/var/lib/shici/vocab.db` |
| 服务环境文件 | `/etc/shici/shici.env` |

已有部署模板把 `VOCAB_ENABLE_OCR` 设为 `false`，基础安装仅安装普通后端依赖，
未包含 `backend[ocr]` 和独立的 PaddlePaddle CPU 运行时。这能够解释按最小模板
部署后 OCR 不可用；但本对话没有连接服务器检查实际环境，不能将模板设置当作
已核实的服务器报错根因。

2GB 内存也使“仅安装依赖并打开开关”的方案存在资源不足风险，因此准备了下面
的轻量配置。它能降低资源需求，不能保证所有图片在 2GB 上都能成功识别。

## 2. 实际增加和修改的代码

### 2.1 修改现有 OCR 适配器

文件：`backend/app/services/ocr/paddle.py`。

新增环境开关 `VOCAB_OCR_PROFILE`，可选 `default` 或 `mobile`。没有设置时保持
默认模型配置；针对这台小内存 ECS，在服务环境中选择 `mobile`。

| mobile 配置 | 实际行为 |
| --- | --- |
| 检测模型 | 明确指定 `PP-OCRv5_mobile_det`，避免依赖库升级后自动选择更大的默认模型。 |
| 中英文识别 | 语言为 `ch` 时使用 `PP-OCRv5_mobile_rec`。 |
| 纯英文识别 | 语言为 `en` 时使用 `en_PP-OCRv5_mobile_rec`。 |
| 运行设备 | 仅允许 CPU；mobile 配置中启用 GPU 或选择其他语言会返回明确错误。 |
| CPU 推理线程 | `cpu_threads=1`。 |
| oneDNN | 显式关闭 `enable_mkldnn`。 |
| 文字识别批次 | `text_recognition_batch_size=1`。 |
| 检测尺寸 | 使用 `max` 策略，检测最长边限制为 1024。 |
| 输入预处理 | mobile 配置下将大图片缩小到最长边 1600；原始上传图片保留。 |
| 方向相关模块 | 不加载文档方向分类、文档矫正和文字方向分类；照片应摆正后上传。 |

同时增加共享推理锁，保护引擎初始化和识别。单个后端进程内已经有 OCR 在处理
另一张图片时，新识别请求得到“OCR 正在处理另一张图片，请稍后重试”，而不是
同时调用同一个推理引擎。识别或初始化失败后也会释放锁，允许重试。

provider 缓存由最多 8 种配置调整为最多 1 种，避免长期缓存多组不用的模型。
这是缓存条目限制，不是整个进程的绝对内存上限。并发保护也只在单个进程内生效；
服务器仍应保持一个 Uvicorn worker，多个进程会各自加载模型。

mobile 模式如果初始化不兼容，会报告错误，不会悄悄退回可能更耗内存的默认模型。
default 模式保留旧版 PaddleOCR 参数兼容路径，并把该路径的初始化错误统一为
`OCRProviderError`。

### 2.2 新增服务器自检脚本

文件：`deploy/verify-ocr.py`。

脚本可以自动生成带有 `hello world vocabulary` 的测试图片，或通过 `--image`
读取指定的真实图片，使用 `--language ch/en` 调用项目的真实 OCR 适配器。
它打印 PaddlePaddle / PaddleOCR 版本、识别文本及通过结果；在 Linux 上还打印
进程的 `VmRSS` 和 `VmHWM`，帮助检查当时及峰值驻留内存。

自检不连接 SQLite，不生成应用导入批次。它会创建并清理测试临时图片，也可能
下载或修复 OCR 模型缓存，所以不是完全没有文件写入的命令。
合成测试必须识别出 `hello` 才通过；真实图片模式的通过仅说明识别出文字，
仍需人工检查单词和中文释义是否准确。

### 2.3 补充回归测试

文件：`backend/tests/test_ocr_optimization.py`。

新增 4 个测试函数、5 个参数化测试场景，覆盖：

- 中英文 mobile 模型选择与线程、批次、尺寸配置。
- mobile 参数不兼容时不回退到默认模型。
- 同时识别时拒绝第二个请求，之后可正常重试。
- 初始化失败后释放推理锁。

这些测试使用模拟引擎，验证程序行为与参数，不等同于真实 Linux 模型推理测试。

## 3. 部署材料与配置

准备的部署文件如下，文件路径均相对于项目目录：

| 文件 | 用途 |
| --- | --- |
| `deploy/shici.service` | 早期 ECS systemd 部署模板：服务账号、工作目录、环境文件、单 worker 启动及重启策略。其私有测试描述属于历史阶段，不能直接代表当前公网配置。 |
| `deploy/shici.env.example` | 基础服务器环境模板；OCR 默认关闭，注释指向安装和验证步骤。 |
| `deploy/README.md` | 早期 Linux 安装说明，补充 OCR 安装说明入口。 |
| `deploy/OCR.md` | 通用 Linux OCR 安装、真实图片验证、常见错误及关闭方法。 |
| `deploy/OCR-2GB-WORKBENCH.md` | 针对已经公网运行的 2 核 / 2GB 实例，提供上传、校验、维护备份、安装、自检、网站验收和恢复步骤。 |
| `deploy/ocr-2gb.env.example` | 小内存实例所需的 OCR 和线程环境变量。 |

小内存实例的配置为：

```dotenv
VOCAB_ENABLE_OCR=true
VOCAB_OCR_PROFILE=mobile
OMP_NUM_THREADS=1
OPENBLAS_NUM_THREADS=1
MKL_NUM_THREADS=1
```

部署说明要求在现有环境文件中调整这些变量，保留已有的数据路径、DeepSeek、
Cookie 和公网设置；不直接用示例覆盖现有文件。CPU 服务器的 GPU 选项应关闭，
中英文单词书选择 `ch`。

此前本地还修正 `.env.example`、`backend/app/config.py` 和
`backend/app/api/imports.py` 中“只能本地使用 / 云端应停用 OCR”等说明性文字。
这些注释修正不改变权限判断或导入数据归属逻辑，未列入本次实现补交清单，避免
将这些文件中其他后续功能改动混入 OCR 提交。

## 4. 已交付的增量包

2026-10-01 已在本地生成：

- `deploy-output/ocr-20261001/shici-ocr-2gb-update-20261001.tar.gz`
- 同目录的 `.sha256` 校验文件、`MANIFEST.json` 和独立的 `部署说明.md`。

包内只有 5 个文件：

```text
backend/app/services/ocr/paddle.py
deploy/ocr-2gb.env.example
deploy/verify-ocr.py
deploy/OCR-2GB-WORKBENCH.md
MANIFEST.json
```

其中只有一个文件替换服务器上的运行时代码：`paddle.py`；另一个 Python 文件
是供操作人员手动执行的自检脚本。包是已有应用的增量更新，不能用于从零安装。
测试文件和本次总结不在这个服务器增量包中。

包大小为 15,858 字节，SHA-256：

```text
5ac0c0b44ff8d397c91e9dc7c1f8f45b44fd6dbcaf999c1ea86602048fdb437f
```

2026-10-07 核对时，包清单中的 4 个源码 / 文档文件仍与本地对应文件的摘要一致。
包没有包含数据库、密钥、用户图片、模型缓存或 Python 虚拟环境。
以上 `deploy-output` 路径是本地交付位置，不是 GitHub Release 下载地址。

## 5. 验证结果及尚未确认的事项

2026-10-01 的 OCR 回归测试通过 31 项。2026-10-07 再次使用独立临时数据目录
复核同一组测试，结果仍为 **31 passed**，有 1 项 Starlette / httpx 依赖弃用警告。
OCR 适配器、测试文件和自检脚本的 Ruff 检查也通过。

复核中的第一次沙箱运行在测试数据库初始化阶段失败；在沙箱外、仍只使用全新
临时测试数据库重跑后通过。没有因此调整应用代码或对真实数据库运行迁移。

本对话中仅完成本地实现、打包、说明和总结归档，未代替用户连接 ECS 安装依赖、
上传文件或重启服务。因此以下事项没有服务器实测证据：

- Alibaba Cloud Linux 上依赖安装和模型下载是否成功。
- 2GB 内存下真实页面识别的峰值占用、耗时和稳定性。
- 当前公网网站的 OCR 请求、代理超时与两人使用的整条验收结果。

应用健康接口返回 200 不能证明 OCR 可用；必须执行真实识别。
轻量模式可能漏识别较小的字，且不自动纠正拍摄方向。
图片识别后的 AI 整理仍使用 DeepSeek，AI 整理失败需分别排查其配置。

## 6. 对现有应用的影响边界

此次 OCR 增量适配未增加数据库迁移、未修改数据模型或前端构建产物，也未增加
对外 OCR 端口或修改 Nginx。既有登录、导入批次归属和个人学习状态仍使用原有逻辑。

部署按维护窗口停服务、备份代码和 Python 环境、安装并自检、再启用 OCR 的顺序
进行。关闭 OCR 可将 `VOCAB_ENABLE_OCR` 改回 `false` 后重启服务；若依赖变更影响
应用启动，详细说明提供恢复原 `.venv`、适配器和环境文件的步骤。

本次总结不包含其他词库、同学测试分支或后续功能合并的改动；也不把已有服务器
公网运行状态当作本次 OCR 增量更新已经成功上线的证明。
