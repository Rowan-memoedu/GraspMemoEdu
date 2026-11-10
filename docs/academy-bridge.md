# JetBrains Academy 题库与 IDEA 作答

整批练习对应 CS 题库中的一个题库，Section、Lesson 对应其中的分组，每道题目对应一个 FSRS 状态。数据、目录、读题页面、自由选题、作答历史和复习排程全部使用数学等学科共用的 `TrainingCatalog`、`TrainingService` 和 `web/training.js`。纯说明材料保留为题组内的阅读页，不创建 FSRS 状态，也不构成答题前置门槛。

IDEA 是作答与判题适配器，只负责打开 Task、检查代码和回传结果。它不另建课程学习流程。题目直接进入作答阶段，无“已理解，开始训练”按钮。复习入口仍打开同一题库页面；IDE 题不混入网页判题试卷。

## 使用

在 CS 题库或复习目录打开练习卡，点击“在 IDEA 中打开”。轻量插件通过 `graspmemoedu://academy/open` 接收授权，由 IDEA 原生 ProtocolHandler 和 JBProtocolCommand 分发，再调用 JetBrains Academy 的 `NavigationUtils.navigateToTask` 打开 Task 和代码。

Windows 安装器只登记当前用户的 `graspmemoedu` 协议，直接交给已安装的 IDEA，不改变系统的 `jetbrains` 协议，也不运行额外常驻服务。原 `jetbrains://idea/graspmemoedu/open` 插件命令继续兼容，但当前机器的 JetBrains Daemon 无法转发它，所以网页使用专用协议。

首次进入一轮练习时，插件先存档当前代码和任务 YAML，再恢复作者占位区中的初始模板。重复点击、关闭后继续同一轮，不再次重置。存档留在本机，IDEA 的 Tools 菜单提供“GraspMemoEdu: 打开代码存档”。未知文件不会被清除；只有本机清单登记的可见文本文件参与模板恢复。

自动判题题目继续使用 Academy 的 Check。检查回调保存代码指纹、结果类别、顺序和时间，第一次通过后统一计算本轮评分，网页自动显示结果。没有通过时可以点击“本轮未完成，结束并安排重练”。没有任何有效失败的轮次不评分，也不把环境问题记成忘记。

开放式练习在 IDEA 完成后回网页自评，使用 Again、Hard、Good、Easy 四档。

## 自动评分

首次有效检查直接通过且未主动加入错题本，使用题库共用规则记 5，保存作答记录但不创建 FSRS 卡、不安排后续复习。主动加入错题本或已有排程的题目，一次通过记 Good。首次通过之前，去重后的编译错误记 0.25、运行错误记 0.5、答案错误或资源超限记 1。相同代码指纹和相同错误类别只累计一次。错误负担大于 0 且小于 3，随后通过记 Hard；达到 3 或结束时仍未通过，记 Again。自动判题不产生 Easy。

只排除明确的运行环境或网络故障。无法进一步归类的有效失败按答案错误计入。时间、提示次数不改变评分权重。网页显式创建一轮，首次通过或主动结束时关闭；它替代基于提交间隔推测轮次的办法，继续同一轮不会因跨午夜分拆。

每轮评分交给共享训练服务，更新同一道题的 FSRS Card。初次有效训练也使用 FSRS 学习间隔；提前练习保存作答历史但不推进未到期的排程，与其他题库一致。导入时不把 Academy 的 Solved、Unchecked 或文件修改时间当成历史评分。

## 本机与网站边界

网页生成仅限当前学习者、题目版本和轮次的随机授权。打开链接十分钟内有效；插件领取后用于本轮回传，最长七天。切换身份、退出登录、账号停用、权限撤销或题目版本变化会阻止旧授权继续推进学习。

网站仅保存授权摘要。插件把回传凭据和未确认的检查事件存入本机私密运行目录，失败时重试；网站按事件身份去重，即使网页已继续下一步，重传也不会再次更新 FSRS。源码和旧答案不随检查结果上传。

一个轮次授权失效不会阻塞其他题目的同步。网页已创建新轮次时，旧轮次及其未同步记录保留在本机 `round-history`；它们不混入新轮次，也不补记成已成功上传。正在检查的题目须等检查结束才可打开新一轮。

网页不接受本机路径或命令行。`task_key` 与项目路径的对应关系只存在本机清单。管理员预览继续使用网站的临时状态，不写正式学习记录。

## 准备和安装

准备工具：`scripts/prepare_academy.py`。输入学生课程、作者课程、稳定 Course ID 和 theory 分类清单；作者模板先统一为 IDEA 文档使用的 LF 换行，再按 Java UTF-16 偏移恢复占位区，磁盘上的 CRLF 不进入恢复模板。分类只有 automated、manual、reading，无法识别的题型会停止，不静默丢弃。

转换使用网站现有的安全 Markdown、MathJax 和图片渲染器。包的 manifest 只列目录和 Topic；带本机路径的中间文件、模板清单和旧答案不属于上传包。安装由 `python -m graspmemoedu.academy_import --package <package> --data-dir <data>` 完成，通过现有 `active-content.json` 原子切换。已有课程和 Topic 不覆盖，重复安装返回 unchanged。

安装器会将标记为 `placement: question_bank` 的 Academy 内容机械转换为 `training-banks/<id>.json`。已有的投影式题库用 `python -m graspmemoedu.academy_training_import --data-dir <data> --course-id <id>` 迁移。该操作先备份数据库，保留原 Topic、版本与历史证据，原样复制已有 FSRS Card 和到期时间，并接续未结束的 IDEA 轮次及其授权。旧 Topic 地址跳转到题库，旧写入入口关闭，复习目录只使用题库排程，避免双份卡片。重复迁移不重评分。权限只有在原题目全部已授权时才映射到整个题库；遇到部分授权即停止，不扩大访问范围。测试材料在题库公开前继承测试标记。

插件源代码位于 `idea-plugin`。`Build.ps1` 使用当前安装的 IDEA 262 和 Academy 2026.9 SDK 编译，依赖目标机已有的 Academy 插件；不复制其源码或重新打包其依赖。将生成的 `GraspMemoEduAcademy` 安装到 IDEA 插件目录，并在 IDEA config 目录放置 `graspmemoedu-academy.json`，指定 `api_origin`、`manifest`、`data_dir`。

源码参考：[JetBrains Academy 导航](https://github.com/JetBrains/educational-plugin/blob/2026.9-release/intellij-plugin/educational-core/src/com/jetbrains/edu/learning/navigation/NavigationUtils.kt)、[检查回调](https://github.com/JetBrains/educational-plugin/blob/2026.9-release/intellij-plugin/educational-core/src/com/jetbrains/edu/learning/checker/CheckListener.kt)、[IDEA 协议扩展](https://github.com/JetBrains/intellij-community/blob/master/platform/platform-impl/src/com/intellij/openapi/application/JBProtocolCommand.kt)。

## 验证

### Hyperskill 自建代码检查

已经审核的 Java 代码测试包可以接入同一个 Academy 插件。网页仍打开原题库中的同一道题，使用相同的 attempt、检查轮次、作答历史、错题本和 FSRS Card；只把本轮的判题适配器换成本地 Java 检查。未配置测试包的题和已经开始的官方判题轮次继续沿用 Hyperskill。

网页的“在 IDEA 中打开”、Task 导航、代码存档、首开恢复模板、同轮继续和原生 Check 均复用上述入口。Check 通过现有 `RemoteTaskChecker` 扩展调用 `scripts/code_check.py`，后者复用代码试验的 Java runner，在 WSL 中隔离编译和运行。源码不上传网站；现有 `AcademyListener` 回传版本、代码指纹、类别和反馈，并由共享后端评分。

`scripts/prepare_code_ide.py` 接收当前题库、逐题审核说明、原模板、测试包及正反例程序。它校验模板与题面版本、执行正反例，再将内容寻址测试包写入本机包目录，生成追加的 manifest 和网站 `code-judges.json`。未通过准入的包不会加入清单；已有学习代码不覆盖。参考解只用于准入，不写入学生项目或发布包。批量试验通过不等于题目测试充分，必须保留逐题的来源与覆盖审核。

IDEA 原有 `graspmemoedu-academy.json` 增加 `code_checker` 配置：`python`、`helper`、`packages`、WSL `distribution` 和 Linux `jdk`。现有 `manifest`、`api_origin` 和 `data_dir` 不变。包绑定题面 SHA-256、测试包 SHA-256、语言、引擎及 runner 版本；旧轮次锁定原包。包缺失、校验不符、JDK/隔离环境故障返回 Unchecked，不产生错误负担。网站可在 `revoked` 中撤销有误的包；旧轮次随后停止评分，保留原代码与记录。

网站使用 `python -m graspmemoedu.code_judges --data-dir <data> --input <registry>` 安装绑定。此命令只合并判题元数据，不导入题库、不改题目 ID 或题库版本、不修改学习表。网站备份/恢复包含这些绑定；本机不可变测试包和 manifest 仍需要本机保留，缺包时按环境故障处理。更新包时先准备本机，再安装网站绑定，避免尚未可用的包被选为新轮次。

`tests/test_code_ide.py` 验证同题记录复用、旧官方轮次兼容、版本锁定、包撤销、重复回传、环境故障、授权失效和备份恢复。实机验收必须从原生 Check 进入；直接调用检查回调不能代替完整联动证据。

### Hydro OJ 判题

Windows 定期同步由 `Install-HydroSync.ps1` 注册原生任务 `SUSTech-CS109-OJ-Sync`，默认每小时及当前用户登录时运行 `Sync-Hydro.ps1`。本机配置与回执在 `D:/AppData/GraspMemoEduHydro/sync`；`state.json` 的 `last_success` 和 `counts` 表示最近成功运行，`last-error.json` 保留最近失败，需按时间区分。校园网或登录态不可用时保留上次成功状态，Windows 最多自动重试两次。

同步沿用原 `academy-sustech-cs109` 题库和 `OJ → 题号 → 单题` 结构。仅为新增题目建立源码骨架，不覆盖已有 Java 文件；提交类与含 main 的普通题按原题要求分别处理。中文题面直接保留，英文新题通过既有 Codex Exec 翻译流程处理，并校验原代码与样例块逐字不变。

OJ 历史状态作为指定网站账号的外部事实保存，显示“已提交，尚未 AC”或“已 AC”；AC 同时更新 IDEA 原生题目状态。它不伪造 Academy Check 回调、不创建学习作答或 FSRS 评分，不改写已有卡片或复习时间。当前明确绑定测试管理员 01，Cookie 只用于本机访问 OJ，不上传网站。题库追加引起版本变化时，先备份并校验恢复，再迁移版本和复习点引用；原记录的作答、卡片、时间等值保持不变。

可手动执行 `pwsh -NoProfile -File D:/Projects/GraspMemoEdu/Sync-Hydro.ps1`，或在 Windows 任务计划程序运行上述任务。同步不主动打开已关闭的 IDEA；关闭期间更新课程 YAML，下次打开加载状态，IDEA 运行时通过原生脚本刷新，正在 Check 的轮次延后重试。

插件的 `Educational.remoteTaskChecker` 可以为本机明确绑定的题目调用 Hydro；现有检查回调继续将最终结果交给共用题库评分。配置位于 IDEA config 下的 `graspmemoedu-hydro.json`，包含固定 OJ origin/domain、预期账号、题目到学生源码的映射、Node 与 `scripts/hydro_check.mjs` 路径，以及本机私密运行目录。Cookie 从已有 LiveCookieSnapshot 读取，只发往配置的 OJ，不上传网站。

OJ 题不需要本地标准答案和对照测试数据。转换时不生成其本地 `test` 目录和测试文件登记；其他 Lab/Assignment 保留本地测试。原生 Check 在无本地测试时仍会进入远程判题，并采用 OJ 返回的最终结果。配置只匹配学生项目及明确的任务路径，不接管其他课程。

适配器在提交前核对当前账号及允许语言，持久化提交身份后只发送一次 POST。相同题目、语言、账号、上下文和代码复用原提交并重新查询结果；响应不确定时按本人记录中的唯一注释标记恢复，不盲目重新提交。比赛和作业必须显式绑定 `tid`。判题未结束、系统错误、登录过期和连接失败返回 Unchecked，不记为作答错误。

`scripts/test_hydro_check.mjs <D盘隔离目录>` 验证终态映射、待判轮询、重复检查、响应丢失后的恢复、匹配不唯一时停止，以及账号变化阻断。部署后必须再通过真实 IDEA Check 和真实 OJ 提交验收，合成测试不代替实机结果。

`tests/test_academy_training.py` 验证共用题库契约、首次直接作答、评分映射、重复回传、同一 FSRS 身份、提前练习、迁移与在途轮次接续、复习去重和权限隔离。既有数学题库测试继续验证原行为。`tests/test_academy.py` 保留旧适配器兼容验证，`tests/test_academy_import.py` 验证幂等安装和 UTF-16 占位区。`idea-plugin/test/BridgeCoreTest.java` 检查存档、指纹、路径边界与结果分类。

编译通过不能代替已安装插件的实机验收。实机还需核对网页授权、IDEA Task/代码导航、存档与模板恢复，以及真实 Check 回传；这些操作使用独立验收课程和身份，不消耗正式练习进度。
