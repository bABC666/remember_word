# NETEM 来源词性与多义：诊断、隔离验证与发布前交付

2026-10-07。正式库仅通过 SQLite `mode=ro` 读取；未提交、发布或写入正式数据。
本次保留工作区原有的 16 个已修改文件和既有未跟踪文件。每日 500 新词、补词队列、设置后刷新、全量分页、未学习筛选和来源默认折叠继续回归验证。

## 当前代码与原始文件诊断

实际正式库为 `D:\背单词web\data\vocab.db`。按名称和 `source_type=netem` 定位后，本次 lexicon_id 为 5，有 5528 词。词性、音标仍全部为空；`source_meanings` 仍为每词一个文本元素；没有 NETEM 核心释义记录。结束时正式 schema 仍为 `0015_session_autoincrement`，不存在新增抽取表。

现有 `concise_meaning.py`、模型、API 与 `ConciseMeaningList.tsx` 已实现词性分组和每词性 1–3 个核心释义，且要求确认者、证据及英语语言门槛。本次没有重建这些机制，没有把自动抽取或旧试验确认当作人工确认。

旧 `50_full_lexicon_rehearsal.py` 从含中文 `<div>` 收集文本，丢弃 `grammar` 和嵌套 `li` 边界，再用分号拼接。大小写折叠还可能把神名 `Set` 混入普通词 `set`。旧脚本保留，以维持历史证据复跑；改进通过新命令提供。

三类缺口：

| 类别 | 已证实的问题 | 本次处理 |
|---|---|---|
| 上游缺失或错误 | run 只有共用中文“跑”，并非四个可一一对应的中文义项；above 原文已有“酸”；April 的来源 grammar 是 pronoun | 保留原文与节点，含混或冲突待核，不补造释义/词性 |
| 导入丢失 | grammar、独立义项/翻译节点、英语 POS 标题、旧式词性模板、原文 IPA 没有进入详情数据 | 恢复结构、逐行/节点位置、语言、源文件和正文指纹；逐来源保留全部义项 |
| 未通过展示门槛 | 原词性未知、共享译文、模板未展开、无口音标签的发音、许可标记或未做逐项英中对照 | 保留到待核记录，正文不展示；不生成 confirmed 核心释义 |

原 WikDict 精确区分大小写后匹配 4822 词，带原始 grammar 的也是 4822 词；885 词有多个 grammar，2185 词有多个中文节点，4802 词有原始发音节点。**节点数量不是准确义项数量。** 旧折叠匹配 4832 的历史统计不适用于本次精确匹配策略。

全量中文维基词典缓存有 3433 词的中文抽取值和 5238 词的 IPA，但不能据此证明语言区、词性标题或原文定位。核验的保留原文覆盖其中 956 个修订；4572 词缺少所需修订的保留原文，本次不凭缓存填满字段。没有新增联网来源。

## 来源核验和适用范围

| 输入 | SHA-256 / 核验方式 | 范围 |
|---|---|---|
| NETEM 原始 JSON | `6d71a301321056291902bc4804e223c6926dca0d629a45076a6ca5adab185f62` | 词头及序号参考；不采用其商业中文释义 |
| WikDict 原始 ZIP | `62d6d4a8ccf28c28bbe4ec82ac65fa67fd52c0f34d71294ab1d9d95fa3233830` | 固定 2026-06-23 en-zh 包，索引序号、解压正文偏移、长度和正文 SHA |
| 中文维基词典缓存 | `50c7cda85bdaa731afb0d25caca39c23a8248497c332a8a10014ce3b32978ff6` | 只定位 oldid；缓存值不直接作为本次结构/音标依据 |
| frozen300 原文 JSON | `09650bf2143440c5810b992a35a24ab8171094c6055aca111c02fe2a73a82a46` | 固定 300 修订原文，不能推广为全库覆盖 |
| launch upstream / gap API 响应 | 逐文件对照保留 manifest；已在证据索引登记的 manifest 再对照索引 | 响应中的实际 title、revid、content；仅同词、同修订适用 |

每次计划的 `source_files` 保存实际输入文件、大小及指纹；写入前重验全部输入。完整来源的作者、版本、许可声明、旧修改说明和相关链接从现有 artifact mapping 保留；另写本次抽取修改说明，不覆写旧署名/许可记录。带 CC-CEDICT 标记的原文整体待核。历史 frozen300/top1000 的 AI 试验确认不构成本次人工确认或发布批准。

## 实施阶段与结果

1. **只读调查：完成。** 检查 git diff、真实 schema/数据、现有核心释义门槛、原始文件和来源质量。
2. **100 词解析和隔离重演：完成。** 包含 abundant、above、absent、run、set、play、April，以及无来源的 pole、未展开模板的 passage、多词性的 a 等。先查看逐义原文再加质量门槛。
3. **全量只读预览和隔离事务验证：完成。** 数据仍属候选来源抽取；全量结果没有接受为词典准确率，也没有核心释义确认。
4. **回归、真实桌面/手机、幂等/回滚：完成。** 见后文。
5. **正式发布：未执行。** 必须先审阅本文、逐词预览和来源质量限制，再取得本次明确发布批准；随后接入现有受保护的管理员发布流程，执行新表迁移和审核过的更新范围。当前新命令故意拒绝正式库，不能把它伪装成已获准生产发布命令。

### 最终覆盖率

| 指标 | 100 词 | 全量 5528 词 |
|---|---:|---:|
| 可解析词性及中文义项结构 | 73 (73%) | 3995 (72.3%) |
| 多词性结构 | 22 | 699 |
| 多个完整来源义项结构 | 48 | 1717 |
| 通过当前来源展示门槛，仍未确认核心释义 | 62 | 254 (4.6%) |
| 可展示多个来源义项 | 21 | 115 |
| 有英语原文定位的 IPA | 9 | 790 (14.3%) |
| 有原始发音候选记录 | 98 | 5283 |
| 没有可用中文义项结构 | 27 | 1533 |
| 新增已确认核心释义 | 0 | 0 |
| 改写原词条词性/音标/原始释义字段 | 0 | 0 |

结构恢复统计包括后来因质量而待核的记录。普通界面仅显示独立 `dictionary_extraction` 中通过门槛的来源义项，明确标注“自动抽取 · 未核实”，并优先显示原有 confirmed 核心释义。完整来源义项没有整词 3 条上限；核心释义仍维持每词性 1–3 条。音标是带定位的**来源发音变体**，没有自动选定一个写入 canonical `phonetic`。

逐项英中对照清单 `tools/phase29/netem-rich-pilot-review.json` 只接受 100 词批次内 78 个固定 WikDict 片段；以 word、POS、正文 SHA 和节点 locator 精确绑定。review_kind 为 `automated_source_comparison`，core_confirmation 为 false。未对照的 WikDict 译文全部待核；不从其他词、其他修订或释义猜造字段。

### 代表词原文对照

| 词 | 实际恢复 / 正文结果 | 无法恢复或剔除原因 |
|---|---|---|
| abundant | 固定英语“形容詞”标题及 `# [[大量]]的，[[丰盛]]的`；IPA 保留 3 记录/2 个不同值 | 未选择英美发音中的一个作为 canonical 音标 |
| above | prep 来源片段“多”通过本次自动对照 | “酸”就在固定 HTML 的 `over, on top of` 下，属上游冲突；共享“上边”缺少可靠逐义对应，保留待核 |
| absent | adjective / 缺席，源文 `being away from a place` | 更多中文维基词典缓存义项缺少所需修订原文；WikDict 发音无口音标签待核 |
| run | 保留 verb grammar 和共用译文“跑”的完整原文 | 三个英文解释共用一条中文译文，不能拆成三个独立中文义项；缓存中的四条释义不直接升级为已核实来源 |
| set | 分别保留 adjective/noun/verb 的原文和节点 | 不混入大写 Set；“但是”“游戏”“袖”等上游片段质量存疑，整体待核 |
| play | verb：玩、遊玩、演奏；noun：剧；来源 /pleɪ/ | 打/拉共用英语音乐、表演等多个解释；未把共用译文假装拆成独立常用义项 |
| April | 保留确切大写词头、原 grammar、四月及发音原文 | 来源标记 pronoun 与专名用法冲突；不把它猜改为 noun/专名 |
| cold | 从实际 `{{=a=\|英\|cold}}` 恢复形容词及“冷” | 旧式 a 标记有明确依据；无需推断 |

详细输入、逐义路径、原文、采用/待核理由见 `representatives.json` 和 `plan.json`。`per-word.jsonl` 按词输出结构新增、可展示数量、旧释义、音标候选和失败原因。全量主要待核原因按词计：未逐项核对译文 3815、共用译文无法逐义对应 1446、无明确口音标签 4676、词性标题缺失 256、未展开模板 87、音标格式待核 80、许可标记 26。原因可重叠，不能直接求和作为缺词数。

## 代码边界与验证

新增 `rich_dictionary_parser.py` 只在英语语言区解析明确 POS 标题或结构标记，处理 HTML 义项层级及 wikitext 独立行；不按分号切义，不使用例句、其他语言、词源或相关词表。未编号裸行、未展开模板和共用译文保守待核。独立抽取表 `entry_dictionary_extraction` 只补保存完整来源与结构，不改变 `lexicon_entry`、`entry_source_evidence` 或 `entry_concise_meaning` 的既有语义。

API 的同一序列化路径为词库、详情和今日学习提供结果；`selectin` 批量加载避免每词查询。候选内容和完整固定原文只在默认折叠的证据区可见，正文不把待核词性/义项包装为已确认内容。原导入释义仍保留在详情底部来源说明中；原文、指纹和词性标题位置可核查。

验证：

- 后端相关回归 **118 passed**：语言/词性/义项/噪声边界、API、核心释义契约、固定行证据、500 新词、全量分页、未学习、浏览不写状态、隔离/外键、幂等/回滚。
- 完整前端回归 **209 passed / 23 files**；独立目录生产构建和 TypeScript 通过。
- Playwright + 现有 Chrome，桌面 **1440×1000**、手机 **390×844**，**70 项实际交互检查通过**：play 多词性和超过 3 个完整来源义项、abundant 释义和 IPA、above/April 错误内容未提升、来源初始折叠及展开、今日学习答案初始隐藏和揭示、无横向溢出/框架错误页。
- Browser 插件未提供，采用 bundled Playwright；`browser-qa.json` 保留检查和截图。唯一现有资源警告为 Vite 自动请求 `/favicon.ico` 404，单独记录；应用/API 错误为 0。
- 独立审查的 IPA 字符、旧式 a 词性和待核原文不可展开问题已补红绿用例、修复并复核通过。

未覆盖：真实手机硬件/系统浏览器、全量每词人工语义审查。本次自动原文对照不是人工批准，也不保证普通用户已得到完整常用核心义项；该缺口与上游质量/原文保留范围相关，不应猜填。

## 复跑命令（隔离库）

以下 PowerShell 从 `D:\背单词web` 执行。预览、clone 输出目录需要新名称以保留旧证据；apply 相同命令重跑返回 changed=0，并保留首份回滚收据。

```powershell
$py = '.\backend\.venv\Scripts\python.exe'
$env:PYTHONIOENCODING = 'utf-8'
$out = 'test-artifacts/netem-rich-rerun-NEW'

# 正式库仅只读预览；100 词先行
& $py tools/phase29/netem_rich_import.py preview --database data/vocab.db --out "$out/pilot" --limit 100 --review tools/phase29/netem-rich-pilot-review.json
& $py tools/phase29/netem_rich_import.py clone --source data/vocab.db --database "$out/clone/vocab.db"

# 显式迁移隔离副本；不要省略 db_url
$db = (Resolve-Path "$out/clone/vocab.db").Path.Replace('\', '/')
$env:VOCAB_DATA_DIR = Split-Path $db
$env:VOCAB_REAL_DATA_DIR = 'D:/背单词web/data'
Push-Location backend
& ./.venv/Scripts/python.exe -m alembic -x "db_url=sqlite:///$db" upgrade head
Pop-Location

$digest = (Get-FileHash "$out/pilot/plan.json" -Algorithm SHA256).Hash.ToLowerInvariant()
& $py tools/phase29/netem_rich_import.py apply --database $db --plan "$out/pilot/plan.json" --plan-sha256 $digest --receipt "$out/pilot-apply.json"
# 原样再执行上一行：changed=0，原收据不覆盖
& $py tools/phase29/netem_rich_import.py rollback --database $db --receipt "$out/pilot-apply.json"
# 原样再执行上一行：rolled_back=0

# 扩大只读预览；不是正式写入
& $py tools/phase29/netem_rich_import.py preview --database data/vocab.db --out "$out/full" --review tools/phase29/netem-rich-pilot-review.json
```

当前最终计划：

- `test-artifacts/netem-rich-20261007/pilot-release-preview/plan.json`：SHA `4340d025b1137f8c2a1eca07121e3b62caee9ed85d1618695fb6e9279b976d64`。
- `test-artifacts/netem-rich-20261007/full-release-preview/plan.json`：SHA `c71f1b1238da8f5c896bee6e91e36a399c79dbccc985d4d7bf8115c6335ba696`。
- 配套各目录内 `coverage.json`、`per-word.jsonl`、`representatives.json`。

回归命令：

```powershell
$env:TEMP = 'D:/背单词web/test-artifacts/netem-rich-20261007/tmp'
$env:TMP = $env:TEMP
Push-Location backend
./.venv/Scripts/python.exe -m pytest tests/test_rich_dictionary_parser.py tests/test_rich_dictionary_api.py tests/test_netem_rich_update.py tests/test_concise_meaning_pos.py tests/test_concise_meaning_api_groups.py tests/test_concise_meaning_wikitext_binding.py tests/test_daily_new_words.py tests/test_library_catalog.py tests/test_isolation_guards.py tests/test_schema_foreign_keys.py -q --basetemp=.pytest-tmp-rich-rerun-NEW
Pop-Location
Push-Location frontend
npm test
npm run build -- --outDir ../test-artifacts/netem-rich-rerun-NEW/frontend-dist
Pop-Location
```

浏览器复跑使用 `netem_rich_qa_seed.py --database <隔离副本>` 创建专用 QA 账号，口令只写隔离目录的 `qa-credentials.json`，不在终端打印。专用 API 8897、Vite 5197，API 使用显式 `VOCAB_DATA_DIR` 和仅信任 `http://127.0.0.1:5197` 的 `VOCAB_CSRF_TRUSTED_ORIGINS`，保留 CSRF；前端 `VOCAB_DEV_API_TARGET=http://127.0.0.1:8897`。`netem_rich_browser.cjs` 读取环境变量 `PLAYWRIGHT_MODULE`、`CHROME_EXE`、`NETEM_RICH_QA_CREDENTIALS`、`NETEM_RICH_QA_SHOTS`、`NETEM_RICH_QA_REPORT`。测试后停止这两个专用进程，删除临时明文凭据；不停止正式服务。

## 保留验证、备份与回滚方案

全量隔离：首次写入 **5528**，原样重跑 **0**，局部回滚 **5528**，再次回滚 **0**。回滚后 extension 空表保留，未做 schema downgrade。完整旧表所有行、列和 ID 的 SHA 一致；包含 **204** 条学习状态、**123** 条复习、**16** 条文章暴露、**16507** 条旧来源证据。个人备注和覆写也包含在整行指纹内。另有测试证明 apply 之后新增的复习记录仍保留于局部回滚之后。

证据：`test-artifacts/netem-rich-20261007/full-apply.json`、`preservation-verification.json`。只读 baseline 快照为 `baseline/vocab.db`，SHA `f20dc8f128bb23a43dcac01ba30d685070587a38116d2f12b41c2c33a6cdc179`；`full-db/vocab.db` 已回滚来源抽取，仍保留新表 schema。快照不写入，也不把学习记录清空重导。

正式发布前流程（未执行）：确认发布范围及计划指纹；用现有维护流程停止正式写入并新建当时的 SQLite 一致性备份，核验 integrity/FK 与学习表指纹；由现有受保护管理员流程认证、固定审核计划和代码版本；只执行新增抽取表迁移和按 entry ID 的增量来源更新；前后逐表保留比对；恢复服务并进行验收。现在的旧 baseline 仅证明本次重演，不能替代发布当刻备份。

优先回滚方式为按收据**只恢复/撤回本批来源抽取行**，先检查当前 payload SHA 没有后续变化；不删除词库、不改变条目/学习 ID，不恢复整库覆盖后续学习。全库备份恢复仅用于已停写且数据损坏的人工恢复场景；正常撤回保留学习进展和新表证据结构。

本次尚未接通正式写入入口或执行正式 migration，避免工作区未提交改动被混入既有固定 commit 的发布授权。需用户对本次明确范围作最终确认后，才进入受保护正式发布环节；历史试验和本次自动对照清单均不是该批准。
