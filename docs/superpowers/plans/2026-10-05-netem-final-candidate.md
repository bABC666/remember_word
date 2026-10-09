# NETEM 默认库首发候选定稿 Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this user-authorized slice inline.

**Goal:** 在已提交修复上冻结可复核的首发候选，落实来源义务并完成隔离验收。

**Architecture:** 在公共导入的冻结 mapping 中增加可选 attribution；无 schema 变更，旧导入保持兼容。候选文件按来源分别保留许可，用户展示读取数据库中的冻结声明。

**Tech Stack:** Python / SQLAlchemy / SQLite / React / Vitest。

**Spec:** 本次用户指令；`docs/V1.2-NETEM-V2-FIRST-LAUNCH-DECISION-2026-10-05.md`。

## Global Constraints

- 个人与朋友学习，不超过 10 人，不商用；允许词库下载和导出；用户数据不对外共享。不得补充运营事实。
- 保留 NETEM 首见成员和顺序、WikDict 主用、整词缺 WikDict 才以中文维基词典补缺。
- 使用 17 词／29 段修复，剔除既知 13 条释义；语义复核只限 build、collect、locate、outward、operation。
- 不写生产库、不推送、不新增下载／导出功能、不启动全库词义合并；保留原三份未跟踪文档。

## Review Focus

- 固定包可定位而无逐词修订号：完整性不应误报。
- 被剔除文本不能由 raw / 候选证据泄漏。
- 旧修订的原许可与改编许可须分别说明。
- 声明字段不得输出任意脚本链接或本机存储路径。
- 私有 TXT／CSV、独立进度、选库持久性保持兼容。

## Tasks

- [x] 核官方 NETEM LICENSE、CC 3.0／4.0、WikDict／DBnary、Wikimedia 现行及旧条款；写逐源直接依据与五词账本。
- [x] 为冻结署名映射的导入、显示及错误链接拒绝写行为测试；观察失败后实现；复跑旧来源测试。
- [x] 由固定修复 CSV 新建最终候选，删除 13 条及五词不能确认的部分；添加逐源声明、固定位置和内容指纹；拒绝覆盖冻结目录。
- [x] 以最终包建全新隔离库；全量核数量、词序、空义与来源，回归私有词库；构建前端并做桌面／移动浏览器验收。
- [x] 完成门禁、只读生产指纹复核与三文档指纹复核，记录生产副本预演条件；本片本地提交 SHA 在最终交付中报告。

本指令已给定范围与执行授权，本片不再追加设计审批步骤。执行记录与验收结果写入最终决策文档。
