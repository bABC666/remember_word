# 拾词：2 核 / 2GB 阿里云 ECS 的 OCR 更新

## 交给下一段 ChatGPT 对话的背景

服务器：阿里云 ECS `ecs.e-c1m1.large`，2 核 CPU、2GB 内存、40GB 系统盘、
Alibaba Cloud Linux 3.2104 LTS 64 位。拾词**已经在公网运行**。
代码在 `/opt/shici/current`，服务为 `shici.service`，数据库为
`/var/lib/shici/vocab.db`，数据库迁移和管理员设置已经完成。

本次只部署 OCR 更新：安装 CPU 依赖，启用轻量模型并验证网站图片识别。
请先检查实际服务路径，逐步指导用户在阿里云 Workbench 操作，每一步确认结果后
再继续。不需要重建数据库、运行迁移、重建前端或更改网站公网访问方式。

建议先验证现有 2GB 机器是否够用。轻量模型和限制并发能减少资源使用，但不能保证
2GB 上所有图片都成功；以服务器自检、实际页面识别及 OOM 日志为准。

## 更新包内容

上传包名：`shici-ocr-2gb-update-20261001.tar.gz`。

| 文件 | 用途 |
| --- | --- |
| `backend/app/services/ocr/paddle.py` | 增加 `mobile` 配置，明确选择 PP-OCRv5 mobile 模型；限制检测尺寸、识别批次与 CPU 线程；阻止同时调用共享推理引擎。 |
| `deploy/ocr-2gb.env.example` | 5 个 OCR / 线程配置，添加到现有服务环境文件。 |
| `deploy/verify-ocr.py` | 生成英文测试图片并执行真实 OCR，也能识别指定图片；不连接 SQLite。 |
| `deploy/OCR-2GB-WORKBENCH.md` | 本说明。 |
| `MANIFEST.json` | 文件路径和 SHA-256 校验值。 |

包里没有数据库、密钥、上传图片、模型缓存或虚拟环境。它是针对现有拾词的增量包，
不能用来从零安装整套应用。

`mobile` 配置支持 `ch` 和 `en`，使用 CPU。`ch` 是中英文单词书所需的混合识别模型；
`en` 只适合纯英文材料。关闭文档方向分类、矫正和文字方向分类，因此请上传摆正的图片。
较大的输入图片缩小到最长边 1600，检测最长边限制为 1024，文字识别批次为 1。
这会牺牲部分小字识别能力；遇到小字漏识别时，可裁剪成较小区域分次上传。

两个人可以照常使用学习功能；同时发起 OCR 时，第二个识别请求会提示
“OCR 正在处理另一张图片，请稍后重试”，稍后重新执行即可。
后端只缓存最近一种语言的引擎，切换语言后首次识别可能重新加载模型。
保留单个 Uvicorn worker，多个 worker 各自加载模型会增加内存。

## 1. 登录 Workbench，检查现状

在服务器终端执行：

```sh
uname -m
free -h
df -h /opt/shici /var/lib/shici /tmp
sudo systemctl show shici -p User -p WorkingDirectory -p ExecStart -p EnvironmentFiles
/opt/shici/current/.venv/bin/python --version
test -f /opt/shici/current/backend/app/services/ocr/paddle.py
```

确认实际用户为 `shici`，实际 Python 位于上述 `.venv`，服务环境文件为
`/etc/shici/shici.env`。若路径不同，请让 ChatGPT 按实际路径调整后面的命令。
`uname -m` 应为 `x86_64`；其他架构先查 Paddle 官方安装说明。
磁盘必须能容纳原虚拟环境备份、新增 Python 依赖及模型下载。
记录当前可用内存和 swap。此说明不自动创建 swap；swap 也不等同于物理内存。

## 2. 上传并核验更新包

在 Workbench 上传本地更新包到服务器 `/tmp/`。如果界面上传到别处，移动到：

```text
/tmp/shici-ocr-2gb-update-20261001.tar.gz
```

```sh
sha256sum /tmp/shici-ocr-2gb-update-20261001.tar.gz
tar -tzf /tmp/shici-ocr-2gb-update-20261001.tar.gz
```

对照随包提供的 `.sha256` 文件确认校验值一致。文件列表应与上表一致，不能出现
绝对路径、`../` 或额外的数据文件。创建全新临时解压目录：

```sh
OCR_UPDATE_DIR=$(mktemp -d /tmp/shici-ocr-update.XXXXXX)
tar -xzf /tmp/shici-ocr-2gb-update-20261001.tar.gz -C "$OCR_UPDATE_DIR"
printf '%s\n' "$OCR_UPDATE_DIR"
```

记下输出路径。后续命令须在同一终端执行；重新连接时，先把 `OCR_UPDATE_DIR`
重新设为刚才打印的路径。

## 3. 在维护时段停止服务并备份

停止服务后网站暂时不可用。只备份本次会修改的代码、服务环境和 Python 环境：

```sh
sudo systemctl stop shici
sudo test ! -e /opt/shici/ocr-backup-20261001
```

第二条命令必须成功；如果目录已存在，先停下来核对上次操作，不能覆盖它。
然后执行：

```sh
sudo install -d -m 0700 /opt/shici/ocr-backup-20261001
sudo cp -a /opt/shici/current/backend/app/services/ocr/paddle.py /opt/shici/ocr-backup-20261001/paddle.py
sudo cp -a /etc/shici/shici.env /opt/shici/ocr-backup-20261001/shici.env
sudo cp -a /opt/shici/current/.venv /opt/shici/ocr-backup-20261001/venv
```

备份目录仅 root 可读。不要在备份位置运行 Python；它用于恢复到原 `.venv` 路径。
若任一步失败，先查明原因；尚未更新时可 `sudo systemctl start shici` 恢复网站。

## 4. 更新文件并安装依赖

```sh
sudo install -o shici -g shici -m 0644 "$OCR_UPDATE_DIR/backend/app/services/ocr/paddle.py" /opt/shici/current/backend/app/services/ocr/paddle.py
sudo install -d -o shici -g shici -m 0755 /opt/shici/current/deploy
sudo install -o shici -g shici -m 0644 "$OCR_UPDATE_DIR/deploy/verify-ocr.py" /opt/shici/current/deploy/verify-ocr.py
sudo install -o shici -g shici -m 0644 "$OCR_UPDATE_DIR/deploy/ocr-2gb.env.example" /opt/shici/current/deploy/ocr-2gb.env.example
sudo -u shici /opt/shici/current/.venv/bin/python -m pip install paddlepaddle==3.3.0 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
sudo -u shici /opt/shici/current/.venv/bin/python -m pip install 'paddleocr==3.7.0' -e '/opt/shici/current/backend[ocr]'
sudo -u shici /opt/shici/current/.venv/bin/python -m pip check
```

每条命令必须成功才继续。版本固定为现有本地环境的 PaddlePaddle 3.3.0 和
PaddleOCR 3.7.0；本地回归已验证参数和并发控制，Linux 实际推理仍需下一步验证。
安装和首次下载模型可能较慢，等待命令结束。

如果缺少 `libGL.so.1` 或 `libgomp.so.1`，在 Alibaba Cloud Linux 用以下命令
查找对应系统包，再根据结果安装；不要修改系统 Python：

```sh
sudo dnf provides '*/libGL.so.1'
sudo dnf provides '*/libgomp.so.1'
```

安装失败时，可按第 8 步恢复原 Python 环境和代码，先恢复网站。

## 5. 服务仍停止时，验证真实推理

自检使用与服务器服务相同的数据目录，但不打开数据库。以 `shici` 账号运行：

```sh
cd /opt/shici/current/backend
sudo -u shici env PYTHONPATH=/opt/shici/current/backend VOCAB_DATA_DIR=/var/lib/shici VOCAB_ENABLE_OCR=true VOCAB_OCR_PROFILE=mobile OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
  /opt/shici/current/.venv/bin/python /opt/shici/current/deploy/verify-ocr.py --language ch
```

它自动生成英文图片、下载中英文模型、识别并打印 `hello world vocabulary`。
成功标志为 `PASS: OCR recognized ...`。最后的 `VmRSS` 是进程当时的驻留内存，
`VmHWM` 是该进程的驻留内存峰值（单位 kB）；这不是整台服务器或所有实际图片
所需的内存上限。

模型应存放在 `/var/lib/shici/ocr-models`。若现有服务设置了
`PADDLE_PDX_CACHE_HOME`，自检命令也须使用它，避免下载到不同位置。

再上传一张清晰、摆正的真实中英文单词页到 `/tmp/shici-ocr-test.png`，确保
`shici` 可读，执行：

```sh
sudo -u shici env PYTHONPATH=/opt/shici/current/backend VOCAB_DATA_DIR=/var/lib/shici VOCAB_ENABLE_OCR=true VOCAB_OCR_PROFILE=mobile OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
  /opt/shici/current/.venv/bin/python /opt/shici/current/deploy/verify-ocr.py --language ch --image /tmp/shici-ocr-test.png
```

真实图片自检的 PASS 仅表示识别出了文字，请人工核对单词和中文释义是否正确。
如果网站要选择 `en`，把命令的 `--language ch` 改为 `--language en` 再验证。

运行时可在另一个 Workbench 终端观察：

```sh
free -h
sudo journalctl -k -n 100 --no-pager
```

若进程被 `Killed` 或内核记录 OOM，2GB 在当前负载下不够。先保持 OCR 关闭并恢复
网站，再评估扩容到 4GB 或更多；不要仅因为模型文件很小就判定内存一定足够。

## 6. 设置服务环境并恢复网站

编辑**现有** `/etc/shici/shici.env`，保留现有内容，把下面五个变量各设一次：

```dotenv
VOCAB_ENABLE_OCR=true
VOCAB_OCR_PROFILE=mobile
OMP_NUM_THREADS=1
OPENBLAS_NUM_THREADS=1
MKL_NUM_THREADS=1
```

不要直接用示例文件覆盖现有环境文件。检查是否有同名重复项或其他 service
drop-in 覆盖配置。保留已有公网网站、Cookie、安全和 DeepSeek 设置。
服务仍使用原有启动命令，确认 Uvicorn worker 数量为 1。

```sh
sudo systemctl start shici
sudo systemctl status shici --no-pager
sudo journalctl -u shici -n 100 --no-pager
```

如果实际仍监听 `127.0.0.1:8000`，可执行：

```sh
curl -fsS http://127.0.0.1:8000/api/health
```

若实际端口不同，使用第 1 步核对的地址。健康接口返回 200 只表示应用启动正常。

## 7. 在现有公网网站验收

1. 以管理员登录现有网址，进入设置页，确认 OCR 可用。
2. 中英文单词书选择 OCR 语言 `ch`，“使用 GPU”关闭。
3. 上传单页测试图片，执行 OCR，核对英文和中文释义。
4. 再执行 AI 整理；该步骤仍依赖 DeepSeek，不能把 AI 错误当作 OCR 错误。
5. 两人同时尝试 OCR，确认一个请求正常识别，另一个得到稍后重试提示。
6. 使用几张实际页面观察内存、服务日志和学习页面响应，再决定是否长期启用。

如果公网 OCR 请求出现 504 或超时，而服务端推理仍能成功，核对现有反向代理
及应用网关的请求超时；Nginx 的相关项是 `proxy_read_timeout`。先预下载模型，
再根据真实单页耗时设置适当超时，修改前让 ChatGPT 检查现有配置。
无需为 OCR 新增公网端口；本包不修改 Nginx、域名或现有公网访问配置。

## 8. 恢复方法

只是关闭 OCR、保留新增依赖：把现有环境文件里的 `VOCAB_ENABLE_OCR` 改为
`false`，然后 `sudo systemctl restart shici`。导入记录和模型缓存可保留。

若依赖安装或更新影响了应用启动，完整恢复本次备份。先确认第 3 步备份确实完整，
并确认下面的失败环境保留路径尚不存在：

```sh
sudo test -x /opt/shici/ocr-backup-20261001/venv/bin/python
sudo test -f /opt/shici/ocr-backup-20261001/paddle.py
sudo test -f /opt/shici/ocr-backup-20261001/shici.env
sudo test ! -e /opt/shici/current/.venv-ocr-failed-20261001
```

全部成功后执行：

```sh
sudo systemctl stop shici
sudo mv /opt/shici/current/.venv /opt/shici/current/.venv-ocr-failed-20261001
sudo cp -a /opt/shici/ocr-backup-20261001/venv /opt/shici/current/.venv
sudo cp -a /opt/shici/ocr-backup-20261001/paddle.py /opt/shici/current/backend/app/services/ocr/paddle.py
sudo cp -a /opt/shici/ocr-backup-20261001/shici.env /etc/shici/shici.env
sudo systemctl start shici
sudo systemctl status shici --no-pager
```

原 `.venv` 恢复到原路径，避免虚拟环境脚本中绝对路径失效。数据库没有参与此
更新或恢复。保留失败环境与备份，确认网站恢复后再决定如何清理。

## 本地验证与限制

本地 OCR 回归测试通过：模型参数、并发保护、失败后锁释放、图片缩放、批次结果
复用、模型缓存修复，共 31 项。服务器安装、模型下载、实际内存和公网整条流程
尚未执行，必须按上述步骤验收。

官方参考：
[PaddlePaddle Linux 安装](https://www.paddlepaddle.org.cn/documentation/docs/en/install/pip/linux-pip_en.html)、
[PaddleOCR 模型及推理参数](https://paddlepaddle.github.io/PaddleOCR/main/en/version3.x/pipeline_usage/OCR.html)。
