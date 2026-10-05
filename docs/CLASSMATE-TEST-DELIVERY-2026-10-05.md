# 同学私有测试分支验收记录（2026-10-05）

按负责人最后更新：同学已经是该项目协作者，只需推送到现有私有仓库。本片没有发送新邀请或更改权限，不以现有访问权限冒充已核验的Read权限。

- 分支：[codex/netem-classmate-test](https://github.com/bABC666/word/tree/codex/netem-classmate-test)。完整交付提交请以远端分支HEAD为准，本记录本身通过后续文档提交附加。
- 两处记录遗漏的独立修正提交：`865e7e8ffc5286ef4a746086ac77d4db71c69038`。
- 隔离版本代码提交及全新GitHub克隆验收对象：`5699786580c6835f51a7320b56d8827e62931b1b`。
- 推送前后远端main均为`eeb7f7c9ab9ed9e856ff2eebd799fcfb3ad05c9d`，为本分支祖先。只推测试分支，不强推、不合并、不改默认分支。

初版及最终候选均通过各自fingerprints.json的258项实际文件摘要核验：初版`dc419c118b056fdebc828fbaa71bbbbf387e16d278d1f77bdbb6fe631942f784`；最终`5fcc8dfa504ab1d008ce9f015ab2848a6c65f75cf64474eb8d56188cb42ae146`。

[同学安装说明](../CLASSMATE_TEST.md)提供setup/start/stop和显式Reset入口。固定目录`data/classmate-test`，地址`http://127.0.0.1:8785`。唯一可登录账号`classmate_test`，密码`Shici-Test-2026!`，只用于每位同学自己电脑的合成测试库；bootstrap admin停用且无可用密码。实时AI及OCR未配置，固定材料里的22条AI译释可验收。

273项固定公共附件集合SHA-256：`c59f8a12f00a15c4dcf508d01aacc261ff80fa03ce40663e26c42214937ac296`。
生成后的稳定公共内容SHA-256：`79f70c7a636ddc3d6e5ee364dd26128209677f3f4d653d24b4f3dcdde838c60d`。
新迁移库包含NETEM 5528词、0空义、16507条证据；账号盐和生成时间随机器不同，SQLite文件SHA另记录在本机dataset.json，未提交SQLite文件。不是生产库复制或清洗。

[来源与许可总说明](../assets/classmate-test/SOURCES.md)、逐CSV的SOURCE.json/SOURCE.md、中文逐词署名表及gap/snapshots许可证和原文/历史附件完整随分支提供。NETEM的BY-NC-SA与释义材料BY-SA分别保留，不将混合集合冒充单一许可证；55条中文表选取和22条AI译释的修改说明均可访问。

完整scripts/check.ps1退出0：后端1020通过/30跳过/1项依赖弃用警告，前端21文件200通过，类型检查、lint及构建通过。隔离证明再次运行后端门禁，8项合成数据文件未变化。历史档案人工审计与生产副本断言因不分发相应本机档案而显式跳过；可运行功能测试使用项目自产合成原文夹具。构建器、链接/路径保护、重置、保留数据、账号/权限和PID归属有8项必要测试，受影响测试44项通过。

本地--no-local全新克隆与GitHub单分支全新克隆分别安装自身虚拟环境、npm ci和前端构建，均完成50项端到端断言：普通登录、管理员401/403拒绝、NETEM推荐及计数、来源详情、TXT/CSV私有导入、用户释义、切换词库、独立学习、重启后的选择和进度、完整性及外键，以及Reset重建且目录外兄弟文件不变。安装前两克隆均没有.env、data、test-artifacts、.venv或node_modules，没有借用原仓库隐藏输入。网页另验普通登录、NETEM推荐、完整来源展开、CSV预览/确认、用户提供释义·未核实标记和学习提交，控制台错误0。

Git ls-files与内容扫描：代码提交时676个跟踪文件、1451个可达历史blob，实际生产账号哈希、会话哈希和配置敏感值19项仅在内存比较，发现0。没有跟踪data、生产数据库、会话、日志或.env；不会因允许合成测试口令而豁免其他敏感值。详细无敏感结果见[结构化摘要](CLASSMATE-TEST-DELIVERY-2026-10-05.json)。最终文档提交还需并已安排再次扫描与精确远端克隆冒烟，其结果在最终交付回复记录。

生产只读保护对照：数据库SHA-256始终为`106663a7941baade7e0baf744ae0517f6a269ecb79618df3a8e7058688bb8b30`，WAL始终为`d8950ffbec42681f7780ffef9a049b9616afa3ec1a063f915a2fc76f132d6780`，数据库/WAL/.env/配置四项字节数、摘要和mtime完全相同，27表计数相同；相同数据库与WAL字节证明原用户行未改变。8000健康检查ok，原监听进程PID300220。三份原未跟踪文档字节摘要不变且未提交。原工作目录仍为main，仅含独立文档修正提交。
