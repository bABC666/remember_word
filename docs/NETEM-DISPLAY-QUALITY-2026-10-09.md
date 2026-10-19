# NETEM 学习展示质量修复与发布候选

本次只授权调查、代码修复及隔离验证。正式数据更新必须取得本次最终确认。本文件对应当前代码；冻结提交、计划指纹和审批前备份指纹见 `test-artifacts/netem-display-quality-20261009/code-lock.json`。上次发布的批准、固定计划和代码锁不用于本次。

## 起点与诊断

起点 HEAD 为 `fcca4446dd113fb26db41ddb96dd3d3b37074805`，已跟踪文件无改动。仓库及其父目录没有找到适用的 AGENTS.md。用户列出的四份未提交历史草稿保留，不纳入提交。直接检查当前实现、正式数据库、保留原文和上次发布收据，没有从历史设计重新建库。

正式库 `data/vocab.db` 全程只读。5528 个词条重扫：繁简转换后相同候选 219 词，共用浅层 WikDict 结构的多表达候选 1048 词，超过两条原音标 2138 词；并集 2715 词。交集分别为 205、109、479 词。Windows 简体转换仅用于展示转换和候选发现，不作为语义证明。

WikDict ZIP 的 SHA-256 为 `62d6d4a8ccf28c28bbe4ec82ac65fa67fd52c0f34d71294ab1d9d95fa3233830`，版本 2026-06-23。所有保留的 WikDict 原文都精确匹配该包。NETEM 提供词头和顺序，后补 WikDict/Wiktionary 提供中文及音标，来源说明不混称 NETEM 原始字段。

根因是原界面按中文精确字符串去重、按音标字符串直接展开。旧自动核验证明节点与原文对应，但不能证明同义等价或完整单词读音。learn 的“学习/學習”实际在不同英文用法中；and 的顺承/因果与并列/列举也不能合并。you、February 的一些短音素在英文原文中属于连读或发音变化讨论。

## 已执行的实施计划

1. 全库重扫并验证来源包、正式 payload 和原发布收据。
2. 构建增量学习展示投影，原抽取、原文、来源指纹、定位、许可和既有审核字段完全保留。
3. 在显式 `pilot-db/vocab.db` 完成约 100 词试验，包含所有例词及 bank、lead、read、record、wind、bow、set、fine、double、lease 等反例；验证写入/重复/回滚/重复。
4. 获取全库英文 Wiktionary 固定修订及版权说明，在显式 `full-db/vocab.db` 验证最终 5528 词计划和局部回滚。限流时遵循 Retry-After，单连接续跑、按指纹复用缓存。
5. 完成解析、API、学习保护回归和桌面/手机实际验收；独立审查发现的问题补回归、修复并重生成候选。
6. 保存审批前一致性备份、锁定代码并交付本次候选；取得确认后才执行正式数据更新。

## 整理规则和实际范围

繁简等价必须处于同一原文指纹、英文义项作用域、词性和用法限制中。不同英文作用域，即使简体文本相同也分别保留，并显示必要的用法说明。没有采用相似度、全词字符串去重或跨来源自动语义合并。

明确的少量同义裁定也绑定实际英文上下文，例如 July 的同一月份定义、January 的同一月份定义、or 的连词译法、yellow 的颜色用法；不会推广到相似字符串。其余多表达候选保留待核。每词性通常展示 1–3 条，更多折叠；这不是整词三条上限。有可靠词性释义时，词性未明确的表达移入更多释义，仍保留全部来源。you 的主语/宾语用法不合并。

新音标必须在固定修订的 English → Pronunciation（含编号标题）中成为显式 IPA|en 条目；支持冒号缩进、亲本口音/强弱读标签、a2/q2 单项限制和限定模板位置。IPAchar 音素讨论、其他语言、正文引用、词首/词尾片段不进入普通学习展示。不用长度阈值，也不把原 WikDict 扁平列表的第一条当标准读音。

代表选择按明确广泛口音、词源及声明的词性/用法分组，采用该组词典列示顺序中的完整音位读音。没有声明它是唯一标准读法或统计上最常见读法。weak/unstressed、rare、nonstandard、obsolete、dialect 等明确受限读法不被选为代表。没有足够依据时不硬选，合理完整变体折叠。英音、美音及其他地区标签均来自来源，不从 IPA 字形推断口音。

最终全库结果：

| 项目 | 结果 |
| --- | --- |
| 整理后释义覆盖 | 5528/5528，100% |
| 显式完整 IPA 覆盖 | 5469/5528，98.93% |
| 代表读音覆盖 | 5004/5528，90.52% |
| 简体展示转换 | 2106 个值 |
| 同上下文合并 | 156 个来源值 |
| 219 个繁简候选词 | 144 词可合并；75 词保留不同或未明确上下文 |
| 1048 个浅层结构候选词 | 146 词同上下文已整理；169 词仍有待核表达；733 词是不同或未明确英文作用域 |
| 全库语义待核词 | 172（包含浅层扫描之外的候选） |
| 原抽取音标记录逐项分类 | 16990 条完整读音获支持；98 条确认非单词片段；1952 条完整性依据不足 |
| 暂无显式完整 IPA | 59 词 |
| 有完整变体但暂不选代表 | 465 词 |
| 人工核心确认新增 | 0 |

音标分类按原抽取记录计数，含多个来源中的相同文本，不是独立词数。各类待核集合存在交集，合计 676 个词有语义或代表选择待核原因。1952 条不足依据不等于已确认错误。本次没有完成全库人工英中语义审核，也没有把此前自动来源核验升级为人工核心确认。

## 证据与验收

最终目录为 `test-artifacts/netem-display-quality-20261009`：

- `diagnosis.json`：只读重扫、交集及原来源包指纹。
- `evidence-full/manifest.json`：全部实际 API URL、抓取时间、固定修订和响应 SHA；版权说明原文同样保存。
- `release-final/plan.json`：本次冻结完整增量计划。
- `release-final/preview.md`：5528 词逐词展示预览。
- `release-final/preview.json.gz`：完整逐词原文对照预览的无损压缩副本。
- `final-data-verification.json`：逐词 payload、原抽取相等、受保护表指纹、完整性/外键及样本原文对照。
- `final-rehearsal.json`：最终计划写入/重复/回滚/重复/再写入，变更数 5528/0/5528/0/5528。
- `full-db/final-undo-reapply.json`：隔离最终批次的乐观局部回滚收据。
- `final-browser-preservation.json`：1316 项实际 UI 检查、异常和截图位置。
- `final-ui-preservation.json`：所有既有学习状态 ID/字段、业务数据、备注/覆写、复习记录保留核验。
- `approval-final/before.db` 与 `backup.json`：代码锁定前重新核验当前正式基线后取得的一致性快照和指纹；正式发布仍会停服务再取当刻备份。较早的 `approval-preflight` 快照也保留。

实际环境为隔离 `127.0.0.1:5198 → 8199`，桌面 1440×1000、手机 390×844。Browser 技能未列出，使用现有 bundled Playwright 和已安装 Chrome。最终从全库选 90 个词进行分层和反例抽样；14 张截图已实际查看关键桌面与手机结果。页面身份、非空内容、无框架错误层、无 JS 异常、无异常 API 响应、折叠交互和窄屏无横向溢出通过。还验证完整分页、浏览不生成进度、未学习筛选、每日 500 输入上限、设置保存刷新和队列批次最多 50。

相关前端回归共 67 项，lint 和构建通过；后端相关解析、API、学习契约回归及最终发布保护测试通过。组合运行发现现有 401 筛选清理测试在异步查询启动前检查最后请求，补齐 waitFor 后保持原请求断言，未修改词库或认证产品逻辑。管理员认证、收据篡改拒绝、发布后新增真实复习的局部回滚保留均在临时库中测试，不读取或重置正式管理员口令。

实际 UI 没有提交评分，复习事件新增 0。原有学习状态的所有 ID 和字段保持一致；仅隔离 QA 账号按正常队列逻辑新增 50 个 new 状态。其设置、登录/退出及克隆启动的失效会话清理单独核对，不把它们混称正式学习数据变化。

## 可复跑与幂等性

以下命令从仓库根目录运行。诊断和报告输出限制在 test-artifacts，拒绝覆盖不同内容的已有报告或来源。preview/clone 要使用新目录。apply 重跑同一冻结计划和收据返回 changed=0；rollback 重跑返回 rolled_back=0。原文、计划或后来抽取发生漂移时拒绝写入。

```powershell
$py='./backend/.venv/Scripts/python.exe'
$root='test-artifacts/netem-display-quality-20261009'
$plan="$root/release-final/plan.json"
$digest='edae5780c4af369318b57b5023b13a1a7c0a85df700139bf11965cd1f2220a7b'
& $py tools/phase29/netem_display_diagnosis.py --database data/vocab.db --out "$root/diagnosis-NEW.json"
# 缓存指纹相符时不重复联网；不得改动已冻结 evidence-full。
& $py tools/phase29/netem_display_fetch.py --database data/vocab.db --out "$root/evidence-full"
& $py tools/phase29/netem_display_quality.py --database "$root/approval-final/before.db" --evidence "$root/evidence-full" --out "$root/replay-NEW"
& $py tools/phase29/netem_rich_import.py clone --source "$root/approval-final/before.db" --database "$root/rehearsal-NEW/vocab.db"
& $py tools/phase29/netem_rich_import.py apply --database "$root/rehearsal-NEW/vocab.db" --plan $plan --plan-sha256 $digest --receipt "$root/rehearsal-NEW/undo.json"
& $py tools/phase29/netem_rich_import.py apply --database "$root/rehearsal-NEW/vocab.db" --plan $plan --plan-sha256 $digest --receipt "$root/rehearsal-NEW/undo.json"
& $py tools/phase29/netem_display_verify.py --database "$root/rehearsal-NEW/vocab.db" --plan $plan --receipt "$root/rehearsal-NEW/undo.json" --out "$root/data-verification-NEW.json"
& $py tools/phase29/netem_rich_import.py rollback --database "$root/rehearsal-NEW/vocab.db" --receipt "$root/rehearsal-NEW/undo.json"
& $py tools/phase29/netem_rich_import.py rollback --database "$root/rehearsal-NEW/vocab.db" --receipt "$root/rehearsal-NEW/undo.json"
```

UI 重演使用 `netem_rich_qa_seed.py --database <显式隔离库>` 生成仅本机使用的随机 QA 凭据，再将 `VOCAB_DATA_DIR` 指向该库所在目录、`VOCAB_REAL_DATA_DIR` 指向正式 data、`VOCAB_TEST_MODE=1`。8199 服务仅把 `http://127.0.0.1:5198` 设为受信开发入口，未关闭 CSRF。Vite 的 `VOCAB_DEV_API_TARGET` 指向 8199。使用 `netem_display_browser.cjs`，其 `NETEM_DISPLAY_PLAN` 指向 `browser-plan.json`，其余环境变量显式给出 bundled Playwright、Chrome、隔离凭据、报告和截图路径。不得把 QA 脚本指向正式库或正式服务。

pytest 使用现有 conftest 的隔离保护，并显式指定新的 `--basetemp` 和本次目录中的 TMP/TEMP，避免既有不可写缓存和 C 盘排序临时文件。完整 JSON 较大，过时生成副本及逐词 JSON 已无损压缩，来源证据未删除；无损归档指纹见 `superseded-generated-artifacts.json`。

## 本次正式发布与局部回滚方案（尚未执行）

1. 确认本次 `code-lock.json` 的提交和计划 SHA，重新只读验证正式抽取基线。之后新增学习进展不属于抽取基线漂移，不会被旧快照覆盖。
2. 沿用原启动器进入维护；新 `netem_display_release.py` 必须检查干净代码提交及所有相关可执行文件属于该提交。
3. 验证当前活跃管理员口令；口令只在本机 getpass 或既有 DPAPI 一次性流程处理，禁止输出明文、猜口令或重置账号。
4. 当刻创建 SQLite 一致性 before.db，复核完整性、外键和全业务表指纹。在事务锁内再次核验管理员、来源指纹及逐词重新计算的投影。
5. 只更新 entry_dictionary_extraction 的增量展示投影，原抽取相等；不迁移、不重导、不删词库，不修改任何词库/词条/学习状态 ID。
6. 提交前写局部 undo；结果记录收据 SHA。全业务表在写入前后应相同。重启并按既有认证流程进行正式只读 UI 验收。
7. 如需撤回，本次入口必须同时提供本次计划、计划 SHA、代码 SHA、收据及其固定 SHA；before 的原 payload、parser_version、词条和基线均须匹配。后来抽取改变时拒绝整批回滚。只还原本批抽取，保留发布后的学习记录、文章暴露、备注和覆写；不整库覆盖 before.db。

```powershell
# 只有得到本次最终发布确认后才执行。
$lock=Get-Content "$root/code-lock.json" -Raw | ConvertFrom-Json
& scripts/stop-vocab.ps1
& $py tools/phase29/netem_display_release.py apply --database data/vocab.db --plan $plan --confirm $digest --code-sha $lock.code_sha --operation "$root/production/publish-NEW" --admin admin --production-update
& scripts/start-vocab.ps1

# 本次发布成功收据才可用；禁止用隔离库收据撤回正式库。
$result=Get-Content "$root/production/publish-NEW/result.json" -Raw | ConvertFrom-Json
& scripts/stop-vocab.ps1
& $py tools/phase29/netem_display_release.py rollback --database data/vocab.db --plan $plan --confirm $digest --code-sha $lock.code_sha --operation "$root/production/rollback-NEW" --admin admin --receipt $result.receipt --receipt-sha256 $result.receipt_sha256 --production-update
& scripts/start-vocab.ps1
```

审批前快照不是正式发布时的备份；正式发布会另取当刻备份。当前未执行正式写入、维护停机或账号重置。尚未裁定的 172 词语义候选、1952 条原音标和无代表读音词均以实际范围披露，不以“字段存在”或“页面不空”宣称全库质量已人工通过。
