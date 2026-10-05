# 拾词：同学私有测试

适用 Windows 10/11，先安装 Git、Python 3.13（勾选 Windows py launcher）及 Node.js 22.12 或更新版本。首次安装需要网络下载软件依赖；词典数据全部随仓库固定提供，不抓取在线词典。安装不需管理员账号、API key、生产数据库或任何本机隐藏文件。OCR和实时AI服务未配置；22条AI译释已作为固定公共材料提供。

接受负责人发给你本人GitHub账号的仓库邀请，然后在PowerShell执行：

```powershell
git clone --single-branch --branch codex/netem-classmate-test https://github.com/bABC666/word.git
cd word
git switch codex/netem-classmate-test
.\setup-classmate-test.bat
.\start-classmate-test.bat
```

打开 **http://127.0.0.1:8785**。普通账号 **classmate_test**，密码 **Shici-Test-2026!**。这组公开写明的测试口令仅用于每位同学自己电脑上的合成测试库，请不要用于其他账号。bootstrap admin已停用，口令保持不可用，不提供管理员密码。

停止：`.\stop-classmate-test.bat`。重启：再次运行start。重复setup会保留现有词库、导入及进度；要从头测试，先stop，再执行`.\setup-classmate-test.bat -Reset`，然后start。Reset只删除此检出目录的`data/classmate-test`，拒绝链接目录和链接文件。后台日志也在该目录。遇到8785端口占用会停止启动，请自行关闭占用它的应用后重试。

**只使用这三个classmate入口。** 数据固定在`data/classmate-test/classmate.sqlite3`，服务仅监听本机独立端口。启动不加载`.env`，也不接受通用应用环境变量重定向。来源附件及许可位于[assets/classmate-test/SOURCES.md](assets/classmate-test/SOURCES.md)，逐文件摘要位于fingerprints.json；新库从Alembic迁移构建，非生产库复制或清洗。固定公共供给5528词、0空义、16507条来源证据。

建议验收：

1. 登录后看到NETEM推荐，进入学习、显示答案、提交“会/不会”；刷新并重启后进度保持。
2. 在词库页导入自己电脑上的TXT与CSV，命名私有库；选择它、学习，重启后主动选择及进度保持；切回NETEM比较同词独立进度。
3. TXT每行仅一个词头；CSV使用`word,meaning`表头（UTF-8），可提供自己的释义。CSV导入后查看“用户释义”标记；以下样例可直接粘贴保存。

```text
apple
compass
```

```csv
word,meaning
apple,我的苹果释义
river,河流
```

4. 查看NETEM词条的来源详情，展开出处、固定版本、贡献者、许可和修改说明。搜索build检查中文表选取；搜索set检查AI翻译整理；这些材料不是人工确认短义。
5. 测试stop/start及明确Reset。学习操作和私有导入都只保存在你自己的电脑；不要把含个人内容的数据目录提交Git。

反馈模板：环境（Windows/Python/Node版本、浏览器）｜复现步骤｜预期｜实际｜截图或错误信息。截图请遮盖你自行导入的私人内容；负责人可据此复现，不需提供整个数据库。

范围为个人与朋友，总人数≤10，非商业。不得将私有仓库内容再次公开分发；独立公共词库材料仍保留其原许可权利，详见来源说明。协作者邀请须为Read/pull；若仓库类型不支持只读，负责人先解决权限条件，不能以Write代替。
