# JetBrains Academy 练习与原子卡

每道练习对应一个 CS 原子 Topic、一个原子卡和一个记忆点。整批内容归入 CS 题库中的一个分组，保留原 Course、Section、Lesson 的目录顺序，不作为“学习”中的课程。纯说明材料转换为引论，不创建 FSRS 状态。练习使用现有 `AtomicLearningService` 的数据表和 `FSRSEngine`，不会另外维护熟练度。

## 使用

在 CS 题库或复习目录打开练习卡，点击“在 IDEA 中打开”。轻量插件通过 `graspmemoedu://academy/open` 接收授权，由 IDEA 原生 ProtocolHandler 和 JBProtocolCommand 分发，再调用 JetBrains Academy 的 `NavigationUtils.navigateToTask` 打开 Task 和代码。

Windows 安装器只登记当前用户的 `graspmemoedu` 协议，直接交给已安装的 IDEA，不改变系统的 `jetbrains` 协议，也不运行额外常驻服务。原 `jetbrains://idea/graspmemoedu/open` 插件命令继续兼容，但当前机器的 JetBrains Daemon 无法转发它，所以网页使用专用协议。

首次进入一轮练习时，插件先存档当前代码和任务 YAML，再恢复作者占位区中的初始模板。重复点击、关闭后继续同一轮，不再次重置。存档留在本机，IDEA 的 Tools 菜单提供“GraspMemoEdu: 打开代码存档”。未知文件不会被清除；只有本机清单登记的可见文本文件参与模板恢复。

自动判题题目继续使用 Academy 的 Check。检查回调保存代码指纹、结果类别、顺序和时间，第一次通过后统一计算本轮评分，网页自动显示结果。没有通过时可以点击“本轮未完成，结束并安排重练”。没有任何有效失败的轮次不评分，也不把环境问题记成忘记。

开放式练习在 IDEA 完成后回网页自评，使用 Again、Hard、Good、Easy 四档。

## 自动评分

一次通过记 Good。首次通过之前，去重后的编译错误记 0.25、运行错误记 0.5、答案错误或资源超限记 1。相同代码指纹和相同错误类别只累计一次。错误负担大于 0 且小于 3，随后通过记 Hard；达到 3 或结束时仍未通过，记 Again。自动判题不产生 Easy。

只排除明确的运行环境或网络故障。无法进一步归类的有效失败按答案错误计入。时间、提示次数不改变评分权重。网页显式创建一轮，首次通过或主动结束时关闭；它替代基于提交间隔推测轮次的办法，继续同一轮不会因跨午夜分拆。

每轮结果直接更新同一个原子记忆点的 FSRS Card。初次有效训练也使用 FSRS 的学习间隔，不套用其他学科原子卡的固定首次六小时。导入时不把 Academy 的 Solved、Unchecked 或文件修改时间当成历史评分。

## 本机与网站边界

网页生成仅限当前学习者、题目版本和轮次的随机授权。打开链接十分钟内有效；插件领取后用于本轮回传，最长七天。切换身份、退出登录、账号停用、权限撤销或题目版本变化会阻止旧授权继续推进学习。

网站仅保存授权摘要。插件把回传凭据和未确认的检查事件存入本机私密运行目录，失败时重试；网站按事件身份去重，即使网页已继续下一步，重传也不会再次更新 FSRS。源码和旧答案不随检查结果上传。

一个轮次授权失效不会阻塞其他题目的同步。网页已创建新轮次时，旧轮次及其未同步记录保留在本机 `round-history`；它们不混入新轮次，也不补记成已成功上传。正在检查的题目须等检查结束才可打开新一轮。

网页不接受本机路径或命令行。`task_key` 与项目路径的对应关系只存在本机清单。管理员预览继续使用网站的临时状态，不写正式学习记录。

## 准备和安装

准备工具：`scripts/prepare_academy.py`。输入学生课程、作者课程、稳定 Course ID 和 theory 分类清单；作者占位区使用 Java UTF-16 偏移恢复模板。分类只有 automated、manual、reading，无法识别的题型会停止，不静默丢弃。

转换使用网站现有的安全 Markdown、MathJax 和图片渲染器。包的 manifest 只列目录和 Topic；带本机路径的中间文件、模板清单和旧答案不属于上传包。安装由 `python -m graspmemoedu.academy_import --package <package> --data-dir <data>` 完成，通过现有 `active-content.json` 原子切换。已有课程和 Topic 不覆盖，重复安装返回 unchanged。

容器元数据的 `placement: question_bank` 控制题库归属。目录和权限仍以同一组 Topic ID 关联，题库视图直接读取这些原子卡的状态，不复制到 `training_memory`。已导入内容使用 `python -m graspmemoedu.bank_placement --data-dir <data> --container-id <id>` 迁移：验证所有正文哈希和层级后切换内容指针，不更新正文版本或数据库行；旧课程选择自动失效。迁移前后的内容指针可用于回退。

插件源代码位于 `idea-plugin`。`Build.ps1` 使用当前安装的 IDEA 262 和 Academy 2026.9 SDK 编译，依赖目标机已有的 Academy 插件；不复制其源码或重新打包其依赖。将生成的 `GraspMemoEduAcademy` 安装到 IDEA 插件目录，并在 IDEA config 目录放置 `graspmemoedu-academy.json`，指定 `api_origin`、`manifest`、`data_dir`。

源码参考：[JetBrains Academy 导航](https://github.com/JetBrains/educational-plugin/blob/2026.9-release/intellij-plugin/educational-core/src/com/jetbrains/edu/learning/navigation/NavigationUtils.kt)、[检查回调](https://github.com/JetBrains/educational-plugin/blob/2026.9-release/intellij-plugin/educational-core/src/com/jetbrains/edu/learning/checker/CheckListener.kt)、[IDEA 协议扩展](https://github.com/JetBrains/intellij-community/blob/master/platform/platform-impl/src/com/intellij/openapi/application/JBProtocolCommand.kt)。

## 验证

`tests/test_academy.py` 验证评分、乱序拒绝、跨轮去重、账户授权和 FSRS Card 身份延续。`tests/test_academy_import.py` 验证层级、幂等安装和 UTF-16 占位区。`tests/test_atomic_banks.py` 验证迁移保留卡片状态、题库层级、学习入口排除、权限和测试材料隔离。`idea-plugin/test/BridgeCoreTest.java` 检查存档、指纹、路径边界与结果分类。`scripts/academy_ui_acceptance.mjs` 在独立后台浏览器只读渲染五种布局。

编译通过不能代替已安装插件的实机验收。实机还需核对网页授权、IDEA Task/代码导航、存档与模板恢复，以及真实 Check 回传；这些操作使用独立验收课程和身份，不消耗正式练习进度。
