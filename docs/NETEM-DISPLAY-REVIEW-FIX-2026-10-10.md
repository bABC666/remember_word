# NETEM 展示候选复审修正（2026-10-10）

旧候选 `af902bd8f6bfa202f540dac1d51a5eddc3601615` 暂缓发布。旧计划、原始来源、代码锁、审查与重演资料保留，本文件与新的 `test-artifacts/netem-display-review-fix-20261010/code-lock.json` 对应本次重新审批材料。正式数据库尚未更新。

## 本次实际问题与修正

所有复现均从保留的固定 Wiktionary 修订读取，没有换来源掩盖错误。原始 ZIP、原释义、原音标、许可及定位未改动，未删除或重导 NETEM。初始重扫仍覆盖 5528 词：繁简候选 219、多表达浅层候选 1048、超过两条原音标 2138，并集 2715，原 WikDict 包匹配错误 0。

| 实际问题 | 修正及来源反例 |
| --- | --- |
| plant 英格兰北部 `/plant/` 被宽泛父标签误归 US | 将地域、发音特征、用法限定分轴；局部明确地域覆盖父级地域列表。`/plant/` 保留 Northern England，不作为美音代表；明确 US `/plænt/` 成为代表。 |
| grow 的 GA 因链接被排除 | 合法 Wikilink 按显示文字解码，原模板与位置保留；GA `/ˈɡɹoʊ/` 不再被地区变体挤出。 |
| forget、grocer 等限定语错挂 | 按完整目标序列绑定限定，包含 IPA、IPAchar、enPR、audio 等；后续替代项限定不回挂前项。 |
| 多参数、位置限定、复合标签和父级特征 | positional 参数分别识别；`final` 为发音环境，不是地域；明确子地域不抹去父级 feature，同轴明确相反 feature 才替换；`<<GA>> <<non-æ-tensing>>` 分别识别。comparable、with、bank、can、fan 等原文已测试。 |
| thin、could、play 的代表选择和语法遗漏 | 特殊 th-fronting 等变体不挤掉无条件默认读法；合法 `IPA| en|` 空白参数正常解析；单参数显式 `/.../ [...]` 混合转写恢复 phonemic 与 phonetic 原值。没有证据时不以某一地区 sole 候选强选默认。 |
| 音频父项和同一行平行转写范围丢失 | English audio 父项的地域/结构条件可传给嵌套 IPA；同行转写保留地域和明确 sense/语法 form scope，但不传播 rare 等目标局部限定。rocket、mathematics、extract、portrait 已对照原文。 |
| IPAchar 被过度判为“确认片段” | IPAchar 也能承载完整替代读法，本身不能证明片段。完整性不足继续待核；确认片段限于显式词首/词尾片段及固定原文明确的 you 连读音素、February 音节说明。 |

新增 `netem_pronunciation_context.py` 保存标签的类型、原文模板、原行和字符位置；来源完整性与标签范围是不同判断。含 OR/excluding 等未充分解释的复合条件保留完整表达并进入绑定待核，不扁平化成同时成立的标签，也不进入代表读音。

展示版本更新为 `netem-learning-display-v2`。API 不采纳旧 v1 投影；新发布入口须核验新版本、新计划摘要和新的干净提交。没有沿用旧批准、旧计划 SHA 或旧代码锁。

## 验证范围与结果

提交的真实来源反例文件：`backend/tests/fixtures/netem_pronunciation_context_20261009.json`，25 个固定修订，含来源网址、revision、原 body/response SHA、署名、许可及逐行定位。只将非相关文本替为空行，保留发音原行和行号。测试校验其原行与保留全文相同；预期值独立于候选生成器。

针对性新旧测试 36 项通过；全部相关后端回归 95 项、前端回归 67 项、lint 和构建通过。独立只读复核再次扫保留全来源，未发现新的明确重要阻塞项。此结果是自动结构与范围复核，不是全库人工语义审核。

先在新 `pilot-db/vocab.db` 做 100 词试验，覆盖本次实际问题及旧多词性、弱读、片段、繁简反例，写入/重复/回滚/重复为 100/0/100/0。再在新 `full-db/vocab.db` 对最终计划重演 5528/0/5528/0/5528。逐词 payload 指纹、保留原抽取、全业务表指纹、完整性及外键检查通过。

当前 5528 词中，释义覆盖 5528，完整发音抽取覆盖 5466，代表读音覆盖 5179。全库共 22138 条新来源读音；36 条复杂标签绑定待核，均未成为代表。172 词语义待核保持披露，人工核心确认新增 0。原音标记录分类为完整读音支持 16957、完整性依据不足 2029、确认片段 54；不把“依据不足”宣称已确认错误。

与旧代码对照，2763 词的标签或选择结果变化，其中包含解码、类型和范围保留，不能把该数当作错误词数。每词性 1–3 条、更多释义折叠及原来源默认折叠保持。学习状态 ID、记录、文章暴露、备注/覆写、用户设置和词库/词条 ID 均未被批次更新修改。

实际 UI 本次已重新运行：桌面 1440×1000、手机 390×844，100 个反例及分层样本，共 1494 项检查、28 张截图。JS 异常、控制台错误/警告和异常 API 响应均为 0。除原流程外，加入独立 plant/grow/forget/grocer/thin/could/play 断言，验证错误读音/限定不会因“和生成计划一致”就被放行。完整分页、未学习筛选、浏览不生成记录、500 设置保存后刷新、50 条队列批次和 QA 退出通过。实际查看 plant 桌面、grow 手机等关键截图。

本次仍采用 bundled Playwright 和已安装 Chrome，Browser 技能未列出。URL 仅为隔离 `5198 → 8199`，正式 8000 服务未重启。UI 没有提交评分；原学习状态所有 ID/值不变，复习事件新增 0，仅专用 QA 按正常队列逻辑增加 50 条 new 状态。正式管理员没有读取口令、解密凭据或重置。

## 新审批资料

目录：`test-artifacts/netem-display-review-fix-20261010`。

- `source-label-review.json`：旧代码与新代码的全库标签/代表对照、实际样本原文和定位。
- `full-final/plan.json`：新完整计划；SHA-256 为 `a3f3d5d0c7ad886c2438171c3df4d5b1f380374f55ce1bb4632bdf943972527f`。
- `full-final/preview.md`、`preview.json`：新逐词展示及原文对照预览。
- `data-verification.json`、`full-rehearsal.json`：最终数据与全库局部回滚证明。
- `browser-final.json`、`shots/`：本次新跑的 UI 检查与截图。
- `ui-preservation.json`：本次 UI 后既有学习数据与业务数据保留证明。
- `approval-final/before.db`、`backup.json`：新审批前一致性快照；正式发布仍会另取当刻备份。
- `code-lock.json`：新提交、新计划 SHA、代码门禁和实际验证范围。

旧材料不会冒充本次验收。四份用户指定的历史草稿不纳入提交。

## 复跑、幂等与局部回滚

使用新目录，不覆写旧计划或来源。所有试验必须显式指定隔离库；正式库在下面的诊断/预览阶段只读。复用保留的固定来源并核验所有指纹，不必为了同一修订重新联网。

```powershell
$py='./backend/.venv/Scripts/python.exe'
$root='test-artifacts/netem-display-review-fix-20261010'
$plan="$root/full-final/plan.json"
$digest='a3f3d5d0c7ad886c2438171c3df4d5b1f380374f55ce1bb4632bdf943972527f'
& $py tools/phase29/netem_display_diagnosis.py --database data/vocab.db --out "$root/diagnosis-NEW.json"
& $py tools/phase29/netem_display_quality.py --database "$root/approval-final/before.db" --evidence test-artifacts/netem-display-quality-20261009/evidence-full --out "$root/replay-NEW"
& $py tools/phase29/netem_rich_import.py clone --source "$root/approval-final/before.db" --database "$root/rehearsal-NEW/vocab.db"
& $py tools/phase29/netem_rich_import.py apply --database "$root/rehearsal-NEW/vocab.db" --plan $plan --plan-sha256 $digest --receipt "$root/rehearsal-NEW/undo.json"
& $py tools/phase29/netem_rich_import.py apply --database "$root/rehearsal-NEW/vocab.db" --plan $plan --plan-sha256 $digest --receipt "$root/rehearsal-NEW/undo.json"
& $py tools/phase29/netem_display_verify.py --database "$root/rehearsal-NEW/vocab.db" --plan $plan --receipt "$root/rehearsal-NEW/undo.json" --out "$root/data-NEW.json"
& $py tools/phase29/netem_rich_import.py rollback --database "$root/rehearsal-NEW/vocab.db" --receipt "$root/rehearsal-NEW/undo.json"
& $py tools/phase29/netem_rich_import.py rollback --database "$root/rehearsal-NEW/vocab.db" --receipt "$root/rehearsal-NEW/undo.json"
```

同一计划/收据重跑写入 0，重复局部回滚 0。正式执行仅在取得本次新确认后，沿用当前管理员 getpass 或既有 DPAPI 一次性认证、维护停机、新提交门禁、当刻一致性备份、事务内管理员及原文/投影重核、全业务表保留证明。

```powershell
# 未得到本次最终确认，不执行下面的正式操作。
$lock=Get-Content "$root/code-lock.json" -Raw | ConvertFrom-Json
& scripts/stop-vocab.ps1
& $py tools/phase29/netem_display_release.py apply --database data/vocab.db --plan $plan --confirm $digest --code-sha $lock.code_sha --operation "$root/production/publish-NEW" --admin admin --production-update
& scripts/start-vocab.ps1

# 若需撤回，只用本次正式收据及其固定 SHA；不能使用隔离收据。
$result=Get-Content "$root/production/publish-NEW/result.json" -Raw | ConvertFrom-Json
& scripts/stop-vocab.ps1
& $py tools/phase29/netem_display_release.py rollback --database data/vocab.db --plan $plan --confirm $digest --code-sha $lock.code_sha --operation "$root/production/rollback-NEW" --admin admin --receipt $result.receipt --receipt-sha256 $result.receipt_sha256 --production-update
& scripts/start-vocab.ps1
```

回滚只恢复本批抽取。收据指纹、before 的原 payload/词条/parser_version/冻结基线及 current after 摘要均需相符；后来抽取变更时拒绝整批回滚。不整库恢复审批或发布快照，不覆盖发布后的学习进展。
