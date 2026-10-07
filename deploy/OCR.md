# 在 Linux 服务器运行 OCR

对于已在公网运行的 2 核 / 2GB 阿里云 ECS，请优先使用
[轻量 OCR 增量更新与 Workbench 操作说明](OCR-2GB-WORKBENCH.md)。

拾词的 PaddleOCR 适配器直接在后端进程中运行，支持服务器 CPU。浏览器上传图片，
服务器完成识别，结果仍写入原有导入批次。无需单独开放 OCR 端口。
后续“AI 整理候选词条”仍使用 DeepSeek，须单独配置其 Key。

当前 `shici.env.example` 默认设置 `VOCAB_ENABLE_OCR=false`，基础安装也只安装
普通后端依赖，所以按最小部署步骤安装的实例不能使用 OCR。开启开关前需先安装
PaddlePaddle 和 PaddleOCR，并验证模型能够下载和推理。

以下命令适用于 `deploy/README.md` 中的 Alibaba Cloud Linux 3、Python 3.11、
`shici` 服务账号及 `/opt/shici/current` 布局。在**服务器终端**执行；其他系统、
ARM 架构、Docker 或其他安装目录需按实际环境调整。

## 1. 确认运行环境

```sh
uname -m
free -h
df -h /opt/shici /var/lib/shici
/opt/shici/current/.venv/bin/python --version
```

下面使用 CPU 推理，不需要 GPU。资源需求与模型、图片尺寸和并发有关；首次加载和
实际识别时检查内存，不能仅凭安装成功判断服务器资源足够。
保留现有服务的 `--workers 1`，避免多个 worker 各自加载一份模型。

## 2. 安装 CPU 依赖

在维护时段停止服务，避免安装依赖期间请求使用到正在变更的运行环境。

```sh
sudo systemctl stop shici
sudo -u shici /opt/shici/current/.venv/bin/python -m pip install \
  paddlepaddle==3.3.0 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
sudo -u shici /opt/shici/current/.venv/bin/python -m pip install \
  -e '/opt/shici/current/backend[ocr]'
sudo -u shici /opt/shici/current/.venv/bin/python -m pip check
```

安装失败时先保持 OCR 关闭，执行 `sudo systemctl start shici` 恢复服务，再根据
安装错误修复环境。PaddlePaddle 与 PaddleOCR 是两个独立依赖，仅安装
`backend[ocr]` 不等于安装了 CPU 推理运行时。

官方安装说明：
[PaddlePaddle Linux CPU 安装](https://www.paddlepaddle.org.cn/documentation/docs/en/install/pip/linux-pip_en.html)、
[PaddleOCR 安装](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/installation.en.md)。
`3.3.0` 与现有项目 README 的 CPU 版本保持一致，服务器仍需实际验证。

## 3. 以服务账号验证真实图片

先把一张清晰的中英文单词图片放到 `/tmp/shici-ocr-test.png`，确保 `shici` 可读。
用与服务相同的数据目录、Python 环境运行下列命令。它只识别测试图片并输出文本，
不连接数据库、不写入导入批次。模型缓存保存在 `/var/lib/shici/ocr-models/`。

```sh
cd /opt/shici/current/backend
sudo -u shici env VOCAB_DATA_DIR=/var/lib/shici VOCAB_ENABLE_OCR=true \
  /opt/shici/current/.venv/bin/python - /tmp/shici-ocr-test.png <<'PY'
import sys
from pathlib import Path
from app.services.ocr import configure_paddle_environment, get_paddle_provider

configure_paddle_environment()
import paddle
paddle.utils.run_check()
document = get_paddle_provider(language="ch", use_gpu=False).extract(Path(sys.argv[1]))
if not document.text.strip():
    raise SystemExit("未识别出文字，请更换清晰图片后再次验证。")
print(document.text)
print(f"OCR 验证通过：{len(document.lines)} 行")
PY
```

首次运行会下载官方模型，可能需要数分钟，服务器须能访问模型下载源。
以 `shici` 账号运行是为了确保它可读写模型缓存。若服务环境显式设置了
`PADDLE_PDX_CACHE_HOME`，验证命令也要设置同一个值。

示例使用 `ch` 识别中文释义和英文单词。如果网站设置页选择了 `en`，应将示例
语言改为 `en` 再验证；不同语言可能加载不同模型。纯英文材料可选择 `en`。
CPU 服务器在管理员设置页保持“使用 GPU”关闭。

## 4. 启用并验证网站

编辑现有 `/etc/shici/shici.env`，把唯一的 OCR 开关改为：

```dotenv
VOCAB_ENABLE_OCR=true
```

保留文件中已有的数据路径和 AI 配置；不要直接用示例文件覆盖现有文件。
然后重启并查看状态：

```sh
sudo systemctl restart shici
sudo systemctl status shici --no-pager
curl -fsS http://127.0.0.1:8000/api/health
sudo journalctl -u shici -n 100 --no-pager
```

健康接口通过只说明应用启动成功。登录网站，在设置页确认 OCR 可用，然后上传
测试图片，执行 OCR，并核对中英文识别结果；再执行 AI 整理验证完整导入流程。
图片 OCR 成功而 AI 整理失败时，检查 DeepSeek 配置。

## 常见错误

| 现象 | 检查方法 |
| --- | --- |
| 返回 `VOCAB_ENABLE_OCR=false` | 检查服务实际读取的 EnvironmentFile，修改后重启服务。 |
| `No module named paddle` / PaddleOCR 不可用 | 检查依赖是否安装到服务的 `.venv`，而不是系统 Python。 |
| `libGL.so.1`、`libgomp.so.1` 等共享库缺失 | 按完整错误安装该发行版对应运行库；Alibaba Cloud Linux 可用 `dnf provides '*/libGL.so.1'` 等查找提供它的包。 |
| 模型下载超时或权限拒绝 | 检查服务器网络和 `shici` 对 `/var/lib/shici/ocr-models` 的读写权限。不要删除整个数据目录。 |
| 进程被 `Killed` 或服务反复重启 | 检查 `journalctl -k` 是否记录 OOM，以及识别时的内存使用；确认没有启用多个 worker。 |
| 命令行识别成功，网页请求超时 | 检查反向代理请求超时和服务日志；先预下载相同语言的模型，避免首次请求承担下载时间。 |

## 关闭 OCR

需要恢复基础服务时，将现有环境文件中的 `VOCAB_ENABLE_OCR` 改回 `false`，
再 `sudo systemctl restart shici`。模型缓存可保留，已保存的导入记录仍然可读。
