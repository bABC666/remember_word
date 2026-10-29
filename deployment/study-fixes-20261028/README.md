# 今日学习修复：冻结部署材料

准备日期：2026-10-28；正式发布：2026-10-29。完整变更和验收见[部署总结](../../docs/STUDY-FIXES-DEPLOYMENT-2026-10-29.md)。

本目录保存实际部署使用的补丁及程序，供审查、复现和回滚参考。当前 ECS 实例已经发布，**不要重复执行**。它要求原文件与 `baseline.json` 完全一致；已有 `backup/` 或 `payload/` 时必须先核对现有结果，不能删除检查后强行覆盖。

## 文件

| 文件 | 用途 |
| --- | --- |
| `study-fixes-20261028.tar.gz` | 实际发布包，包含两个后端源码、学习页源码、样式、入口 HTML 和两个构建资源 |
| `package.sha256` | 整包 SHA256 |
| `manifest.json` | 每个发布文件及服务器原文件的 SHA256 |
| `baseline.json` | 4 个必须匹配的原源码文件校验值 |
| `deploy.py` | 校验、备份、隔离演练、代码安装、重启、验收和异常恢复 |
| `probe.py` | 在专用数据库副本中测试真实接口，不创建正式测试账号 |

发布包大小 **159177 字节**，SHA256：

```text
35335b7a62a22f1bd06c3b93812c97a0d5637f4a4389d1d6b9df366350e78fa2
```

## 对匹配旧版本实例的操作

1. 核对服务、代码和 SQLite 路径分别为 `shici.service`、`/opt/shici/current`、`/var/lib/shici/vocab.db`；本机已有 `shici` 系统用户及运行依赖。
2. 建立 `/opt/shici/study-fixes-20261028/`，权限为 `700`。将 tar.gz 以 **`patch.tar.gz`** 名称放入，同时放入 `deploy.py`、`probe.py` 和 `package.sha256`。不要上传真实数据库到 GitHub。
3. 核对上传文件字节数和 SHA256 后，以具备服务管理权限的服务器账号运行：

```sh
/opt/shici/current/.venv/bin/python /opt/shici/study-fixes-20261028/deploy.py
```

脚本检查 tar 条目、文件校验和旧代码基线，保存源码、入口和部署清单及 SQLite 一致性备份；复制现有后端和迁移定义，在 `verification.db` 中执行接口演练。全部通过后才短暂停止服务，将资源和源码安装到位，最后原子替换入口 HTML，再启动并核对健康与静态文件内容。脚本不运行迁移、不写共享 API 配置、不删除旧构建资源。

成功输出 `STUDY_FIXES_DEPLOYED`，写入 `result.json`。正式安装阶段异常则恢复原源码、入口和清单后重启。演练失败时线上版本不变，先查看 `probe.log`，不能跳过演练强行发布。

## 后续回滚

发布前备份位于服务器工作目录的 `backup/`。停服务后可按原路径恢复备份中的四个源码文件、`frontend/dist/index.html` 和 `DEPLOYMENT-MANIFEST.json`，再启动并检查健康接口。旧资源保留，无需删除新资源。没有数据库迁移，不要用旧整库覆盖发布之后的新学习记录。

仓库的 `frontend/dist/` 仍按原规则忽略；此小型压缩包是与实际部署指纹绑定的归档材料，包含其自身入口和资源。新版本开发请修改正常源码并重新构建，不要修改归档冒充已部署包。
