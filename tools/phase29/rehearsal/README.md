# Phase 2.9 真实数据预演脚本（仓库外运行）

这三个脚本在**仓库外**把真实数据跑成"文件 → 只读预览 → 锁定计划"，并把人工裁定量算出来。
它们**不导入任何东西**：不写 `LexiconEntry` / `UserWordState` / `ReviewEvent`，不打开应用数据库，
不发送任何授权询问。三个脚本都通过仓库根 `ruff.toml` 的 `ruff check`。

对应报告：`docs/V1.2-PHASE2.9-REAL-DATA-REHEARSAL-SLICE-RECORD.md`。

## 需要准备的输入（都不在 Git 里）

| 输入 | 说明 | 报告里的指纹 |
|---|---|---|
| NETEM `netem_full_list.json` | 5530 词表原始文件 | §3.1 |
| WikDict `wikdict-en-zh.zip` | 官方 stardict 包（许可原文在包内 `stardict.ifo`） | §3.1 |
| zh.wiktionary 清洗缓存 `zhwiktionary-full-coverage.json` | 按每个词的 `oldid` 抓取并清洗后的结果 | §3.1 |
| WikDict 60 词质量对照 `quality-sample-dump.json` | 抽取器的回归基线 | §3.1 |
| `tools/phase29/phase29-zhwiktionary-pinned-oldids.tsv` | 已入库的 100 词 `oldid` 钉住清单（脚本会读它做交叉核对） | §3.1 |

## 运行顺序

```powershell
# 1) 生成预演包：<package>/sources（CLI 的 --source-root）、notes/、指纹
python 10_build.py   --evidence-dir <证据目录> --package-dir <预演包> --repo <仓库工作区>

# 2) 只读跑 CLI：preview-many / plan（含 --require-ready、边界拒绝、覆盖拒绝、幂等重复）
python 20_run_cli.py --package-dir <预演包> --repo <仓库工作区>

# 3) 把计划变成逐词人工裁定清单与统计
python 30_analyze.py --package-dir <预演包>

# 4) 考研词试点：164 词逐词裁定清单（原值/版本/许可/机器预筛，人工栏位留空）
python pilot_select.py --from-dump --package-dir <预演包>     # 离线复算
python pilot_select.py --refetch --sleep 4 --package-dir <预演包>  # 按 oldid 重取修订
```

- `--evidence-dir` 默认是本次会话的隔离目录（`C:\Temp\dsh-...`），换机器必须显式传。
- `--repo` 只用于两步：读已入库的钉住清单、跑 `python -m app.cli`（用该工作区的 `backend/.venv`）；
  `pilot_select.py` 还会从 `tools/phase29/zhwiktionary_clean_measure.py` 导入**已入库的**清洗规则做复算，
  `--repo` 缺省时自动取本脚本所在的仓库。
- 脚本会记录运行前后 `data/vocab.db`、整个 `data/` 目录与主工作区 `git status` 的指纹，
  用来证明预演没有碰生产数据。

## 产物

| 路径 | 内容 |
|---|---|
| `<package>/sources/` | 转换后的 CSV、`manifest-r1/r2/demo20/r3-exam-corpus/escape.json`、`decisions-*.json` |
| `<package>/out/` | 只读联合预览报告与 6 份计划 JSON（写入后拒绝覆盖） |
| `<package>/notes/03-fingerprints.json` | 原始文件、包内文件、转换文件、脚本的 SHA-256 与统计 |
| `<package>/notes/05-worklist.tsv` | **逐词人工裁定清单**（每词一行：状态、阻断原因、两侧候选值与行号、需要的动作） |
| `<package>/notes/06-analysis.md` | 裁定量、分档、冲突形态、规模外推 |
| `<package>/pilot/pilot-sheet.tsv` | **试点逐词裁定表**（51 列：原值/版本/许可/机器预筛 + 7 个人工栏位，交付时为空） |
| `<package>/pilot/pilot-sheet.md` | 同一张表的人工可读版（按 A/B/C/D/E 组分区） |
| `<package>/pilot/refetch-trace.tsv` | 114 个"页面存在但无释义"词的 oldid 复核轨迹（字节数/SHA-256/诊断/扩大规则候选） |
| `<package>/pilot/zh-pinned-wikitext.json` | 已 pin 修订的本地留档，供离线复算（`--from-dump`） |

## 边界

- 不把任何词典原文放进仓库；`sources/`、`out/`、`pilot/` 全在仓库外。
- `manifest-r1.json`（推荐口径）**不映射 NETEM 的释义列**；`manifest-r2.json` 只用于量化
  "把权属未明的释义当候选会怎样"，其结论是**不要这样做**（见报告 §8.1）。
- `decisions-demo20.json` 是**机器草案**，只用于证明计划能被裁定到 `confirmation_ready=True`；
  真实导入的裁定必须由人工逐词重做。
- `pilot-sheet.tsv` 的 `human_*` 列**必须由人工填写**：机器只做机械核对与风险标注，
  机器建议（`machine_suggestion`）不是裁定；`human_verdict=修订` 时必须填 `human_modified=是`。
- 词频列在预演包里叫 `freq_claimed_mixed_exam_corpus`：它是**来源自述的混合考试语料计数**，
  不得标成"考研真题词频"。
