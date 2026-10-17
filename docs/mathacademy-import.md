# Math Academy JSON 课程导入

`Import-MathAcademy.ps1` 按源课程 ID 接收两个 ZIP，机械转换原有 JSON，并将目标课程及显式前置内容整体安装。原 JSON、缺失信息及图谱来源随内容保存；不调用模型，不补题、答案、解析或依赖。

```powershell
# 干净隔离网站，五门首批课程及其显式前置
./Import-MathAcademy.ps1 -TopicsZip 'D:/Downloads/topic-json.zip' -GraphsZip 'D:/Downloads/course-knowledge-graph.zip' -DataDir 'D:/Artifacts/GraspMemoEdu/isolated-import'

# 已配置正式服务器，仅机器学习 I、II 及其显式前置
./Import-MathAcademy.ps1 -TopicsZip 'D:/Downloads/topic-json.zip' -GraphsZip 'D:/Downloads/course-knowledge-graph.zip' -CourseIds 72,140 -Target production -OutputDirectory 'D:/Artifacts/GraspMemoEdu/ma-ml-prepared'
```

默认五门课程 ID 为 82、74、66、72、140；`-PlanOnly` 只计算闭包。环境复用项目 Python、Node/marked/MathJax/sanitize-html、Tectonic 和 pdftoppm。源 ZIP 不进入仓库或静态前端。转换缓存位于 `D:/Caches/GraspMemoEdu/mathacademy`；转换代码或渲染配置变化会失效派生缓存。

## 内容和访问

所有导入课程与 Topic 在激活前标为测试材料，归入数学学科。可见性沿用测试材料策略：用途为 test 的管理员，或用途为 test 且同时具备学员和高阶学员身份的账号。显式 Topic 权限仍然生效，游客和正式账号不能通过目录、直接链接或另一课程绕过。

Topic 使用 `ma-topic-<源ID>`，课程使用 `ma-course-<源ID>`。一个 Topic 共享正文、内容版本和学习身份；课程成员与外部前置分别保存。目标课程完整导入，关联课程仅包含闭包内的前置，并在目录说明范围。

`availability.saved/readable/trainable` 分别表示保存、阅读、训练资格。不满足现有题量、题面、答案等训练条件时，提供原文阅读与具体缺失原因，不建立学习记录、FSRS 卡片、训练任务或完成状态，也不能解锁下游。抓取时的输入答案、完成标记和私有源字段不进入公开阅读投影。

连续指导合并展示并保留内部身份及标题；训练内容的篇末阅读必须点击继续才能完成 Topic。组合输入采用题型协议 2，整题最多 64 段、128 个控件、16384 字符；只有最后一段提交整题，刷新恢复同题草稿。

## 安装、重跑和恢复

准备包包含哈希清单、目录、图谱、派生正文、私有源 JSON 和可用性报告。转换错误中止激活；原材料缺失保留为内容状态。安装复用既有导入锁，先在 `content-batches/<摘要>` 完成合并与校验，最后原子切换 `active-content.json`。旧内容和学习数据库不被覆盖；同 ID 不同正文报冲突。重复安装报告 `unchanged`。

正式入口通过现有 SSH 和 `host.py run-stage` 执行，先创建已验证备份，再暂停应用完成切换并恢复健康检查。若安装或健康检查失败，恢复上一内容指针并重启原内容。过程不回滚学习数据库；应用源码发布独立于课程安装。站点备份展开保存有效目录、正文、源 JSON 和图谱，恢复无需原活动指针。

带离线 SVG 的 Topic 阅读响应可达数 MB。部署模板在本应用的 API location 启用 JSON gzip，压缩传输但保持响应内容及权限检查不变；已有多站点服务器只增量应用该 location 的压缩配置，不覆盖其他站点。机器学习批次上线时服务内存上限设为 1536 MiB，具体容量仍按主机实际余量核验。

`tests/test_mathacademy_import.py` 使用独立完整合成题验证训练边界；`scripts/mathacademy_interaction_fixture.py` 与 `mathacademy_interaction_acceptance.mjs` 验证 10 段 23 控件、刷新与篇末完成。真实来源另行执行整批转换、共享身份、附件与备份恢复验收，不能用合成样本冒充真实资料完整。
