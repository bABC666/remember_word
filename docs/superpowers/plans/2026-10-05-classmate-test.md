# 同学私有测试分支交付实施计划

> 执行方式：负责人已要求完整实现、全新克隆验收、推送后再停止；本任务直接执行，不派子代理。

**目标：** 从文档修正提交制作可在全新 Windows 安装的隔离测试分支。
**架构：** 固定公共附件随 Git 分发，构建器使用全新 Alembic 库和公共导入服务，再按词形/顺序追加77条释义证据；不读取生产数据库。固定数据目录 data/classmate-test、端口8785；普通账号唯一，admin 无可用口令并停用。
**技术：** Python 3.13、锁定 Python 依赖、Node 22.12+、npm ci、PowerShell 5.1。
**约束：** 不切换主工作区，不推 main，不强推、不合并、不改默认分支；三份原未跟踪文档原字节保留。只邀请精确用户名且 Read/pull。

- [x] Task 1: 验证初版/最终候选全部258成员，修正两处记录，独立提交865e7e8ffc5286ef4a746086ac77d4db71c69038。
- [x] Task 2: assets/classmate-test 保留独立来源/许可证/oldid/修改说明；去除生产词条ID，生成内容清单及SHA。backend/app/classmate_dataset.py 从新迁移库构建；测试覆盖计数、禁用admin、普通账号、无历史及包篡改拒绝。
- [x] Task 3: tools/classmate_runtime.py 固定路径/端口、保留已有数据、显式Reset、拒绝链接/未知库；setup/start/stop BAT+PowerShell。先写真实路径保护与启动行为测试并观察失败，再实现。
- [ ] Task 4: 完整 scripts/check.ps1 在独立工作目录、独立全新合成门禁库上运行；受影响测试和安全扫描通过。基线依赖漂移(SQLAlchemy2.1 Windows URL编码)已定位，测试版锁定2.0.54。
- [ ] Task 5: --no-local 真正全新克隆、独立依赖安装、登录/来源/导入/学习/选库/重启/重置与生产保护对照。
- [ ] Task 6: 推送前只读查询实时main及同名分支，只push codex/netem-classmate-test；从GitHub再全新单分支克隆冒烟，记录精确SHA和链接。提供用户名后完成最低权限邀请。

**重点审查：** data 或子目录为junction/symlink、数据库为hardlink；失败构建的临时库；已有库重装保留；端口被无关进程占用、PID复用；污染环境/.env；来源混合许可与77条AI修改说明。

执行记录：新库8项安全/账号/计数/保留/PID测试通过；44项受影响测试通过。旧原文写入测试改为项目自产合成夹具；档案型历史人工审计显式条件跳过。源码测试与全门禁顺序运行，避免共享pytest临时目录互相清理。私有个人仓库不支持Read协作者，邀请保留待负责人解决权限条件。
