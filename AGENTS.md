# GraspMemoEdu

- 腾讯云发布用服务器 `/usr/local/lib/tencent-cloud-maintenance/host.py run-stage --path <本次暂存目录> -- <发布及验证命令>` 包裹，成功即清理暂存；异常无占用残留由服务器每 15 分钟回收，闲置保留 24 小时。正式数据、运行目录及公开资产不属于暂存。服务器每类备份只保留最近两份，本机转存并校验后精确删除远端；本机离线允许淘汰未转存旧副本。维护源码位于 `D:/Projects/DeveloperGovernance/server-maintenance`，不得恢复旧的多份同机保留策略。

- 测试材料保留，不通过删除内容来隐藏。共用 `test-materials.json` 标记测试课程、Topic、题库和学科指南，仅高阶学员测试账号（高阶且为学员）和管理员测试账号可访问；必须同时满足 `purpose=test` 和对应资格，正式高阶学员、正式管理员、普通测试账号、高阶非学员和游客均不得绕过，包括管理内容库、学员报告、暂停及答疑入口。标记与正文版本、学习记录分离，备份必须包含标记；重新安装或恢复测试材料前先设置标记，禁止先公开再补限制。测试场景重置只允许明确标记的测试材料及启用的高阶测试学员，先备份，保留正式记录。具体配置与验收见 `docs/test-materials.md`、`docs/testing-system.md`。

- 网站可逆下线已准备：用户说“网站下线”执行 `scripts/site_availability.py offline`，说“恢复上线”执行 `online`，按 `docs/site-availability.md` 核验；只切换 Pages 发布及 GraspMemoEdu 公网访问，保留课程、账号和学习数据，不额外二次批准。准备阶段保持在线；下线时不得用日常发布脚本绕过关闭状态，不覆盖待恢复配置或其他网站的 nginx 配置。

- 产品品牌为 GraspMemoEdu，整体多学科框架以 RemNote「搭建掌握式学习平台」（MQrYfdVzLFh4tV96K）及用户后续修订为准。古典衬线风格只用于总首页；数学与全局设置恢复原模板视觉，其他学科沿用同一模板并替换配色、名称与徽标。数学正式课程和物理「运动学进阶」保持原内容。2026-10-05 用户授权恢复英语、语文、生物、化学的原子训练样例、一份英语范式样例，并新增数学测试题库，全部纳入测试材料策略；其余已下线测试内容不自行恢复。共用结构在当前仓库维护，不复制六份网站。
- 学科路由为 `#/subjects/<id>/...`；旧数学路径继续兼容。账号、身份存储与 Topic ID 不因学科拆分改变。正式课程归属、名称及指南由服务器 `/var/lib/graspmemoedu/subjects.json` 配置；配色和首页/导航栏共用徽标统一在 `web/subjects.js` 维护，见 `docs/subjects.md`。不得将服务器或本机路径暴露给公开前端。
- 腾讯云托管的多学科学习应用；Lesson 产品行为以 `D:/Artifacts/GraspMemoEduPlan/01-需求与方案.md`、`04-推送系统补充需求.md` 为准，复习后端以 `docs/review-backend.md` 为准，当前实现/运维以 README 为准。
- 主页布局以 `D:/Artifacts/MathTopicPipeline/MathAcademy-learn-主页复刻需求.md` 为准；Course「再探小学算术」组织 Topic「数字化为字母」，不改变其 ID、版本和既有进度。网站主页产生真实 Lesson 和到期 Review 任务，尚未接入 XP 或测验。
- 正式 GUIDE 由服务器 `subjects.json` 指定并单向发布；数学和物理共用 `/var/lib/graspmemoedu/subject-guides/shared/guide.md` 与相对 assets，本地编辑源在 `D:/AppData/GraspMemoEduGuide/guide.md`。保留分层大纲，标题与列表可展开收起；学员和高阶说明按 `docs/guide-visibility.md` 独立投影，游客与管理员可阅读全部。配图用本网站真实界面。更新指南不能夹带技术配置说明或暴露其他文件。
- 技术栈：Python/FastAPI/SQLite，原生 HTML/CSS/JS，离线 MathJax SVG；复用 `D:/Projects/MathAnswerJudge`，不在网页判题。
- 正式启动由腾讯云 `graspmemoedu.service` 管理，只监听 127.0.0.1:18768。Windows 启动脚本仅用于显式指定的隔离开发目录。8765 属于 AnkiConnect，不要终止。
- 活动开发源码仅在本仓库；正式数据在腾讯云 `/var/lib/graspmemoedu`，本机 `D:/AppData/GraspMemoEdu` 仅作回滚快照及原稿存档，证据在 `D:/Artifacts/GraspMemoEdu`。课程正文、答案表、数据库、配对信息和密钥不进入 Git。
- 修改后运行 `D:/AppData/GraspMemoEdu/venv/Scripts/python.exe -m unittest discover -s tests -v`。实际学习流程须用独立验收目录和真实判题核验，不能消耗正式进度。
- 课程默认只做机械导入：解析结构、按字段注入、搬运附件及校验来源一致性；不得逐题检查数学、答案、图注或配图细节。API 仅可用于极少量抽样，检查整体性损坏，不作为逐题语义门禁。后续只局部修复，不整篇重跑，不增加全量细节检查。
- 界面术语以 `docs/ui-language.md` 为准，完整对照表从生产词库生成并检查；Introduction 为“指导”（含 v2），纯引论 Topic 类型标为“引论”，Guide 为“指南”；指导的自定义标题保留。纯范式 Topic 仍先选择例题是否掌握；Example/Practice 归属前面最近的 Introduction。混合 Topic 按每节指导、原子初学、范式训练推进，只在继续/下一页插入当前节可推送的到期卡；进入下一节后不再插上一节卡，范式暂停不阻断原子复习，详见 `docs/unified-topic.md`。UI 本地化不得翻译课程正文、答案或解析。
- 作者 Markdown / v2 JSON 导入使用 `Import-Topic.ps1` 或 `author-prepare` / `author-install`，规范见 `docs/author-import.md` 和 `docs/topic-format-v2.md`。复用原转换器，原稿 Explanation 机械映射为判题参考，不经 LLM 改写；来源包留本机。普通 Topic 只新增，纯引论允许同 ID 更新并保留旧版本与学习进度；不自动扩大账号权限，安装后重载后端。纯引论无定时复习、不进入知识图谱，已学过且教学内容变化才推送重读。
- 字段固定归属：定义、定理、性质、识别特征、范式属于 Introduction；要求、题源属于题目正面；巧法、Idea / idea 属于同题背面的 Explanation。纯 RemNote 内引用节点就是 idea。普通字段不能按原位置保留，转换时按固定归属整理并校验，保留内容与身份；范式以 Mermaid 表达整数顺序步骤、小数分支。既有产物只做显示兼容并同步答案侧引用可见范围，保留原始内容版本和学习进度。
- 本地插图使用文档目录内相对 PNG，来源包绑定附件哈希，显示时内嵌受控 data URL；安装和备份保留原附件，不审查配图教学细节。新建整门课程可用 `scripts/install_author_course.py`，先验证全部来源包再切换目录，沿用已存在图谱的 Course/Topic ID。局部修复只更新受影响字段、附件及哈希并留痕，不重跑整篇导入。
- 图谱与 Topic 分开导入，规范见 `docs/course-graph.md`。`Import-Graph.ps1` / `graph-import` 按整份课程图谱反复更新，以稳定 `topic_id` 关联，可先于正文导入。图谱不修改正文、题库、权限或学习进度；版本 1 只含 Topic 内部前置关系。正式图谱数据和历史保存在服务器私密数据目录，不提交私人课程内容到源码仓库。
- Practice 输入兼容连续整数编号和每例 5×3 的分组编号 1.1–5.3。分组稿展平显示普通序号 1–15，`source_practice` 保留分组；拒绝重复、缺号、乱序与同例混合编号。15 题库使用 `practice_queue.py` 的学习者/Example 持久队列：新 Lesson 先取第 1、4、7 题，初始目标 3 题，每错增加 2 题至 5/7/9，最多追加 6 题，第 4 次错暂停且不再补题；组内首次错题追加其余两题到队尾，缺题时先未推送再最久未推送，同轮不重复。复习的初始题量、评分及独立初始化抽法保持原规则，跨 Lesson/复习保留展示记录。同组补题标记持续至清空记录，暂停/解锁不重置。新 Lesson 保存 lesson-v2 策略，旧已开始轮次保留原题序和题量，旧历史不回填；详见 `docs/practice-queue.md`。
- 课程版本不匹配须保留旧进度；暂停与评分由 SQLite 事务决定；在途判题不持有数据库锁，旧轮次结果不能推进新轮次。
- 后端只绑定回环地址。用户已授权管理员网页解除暂停：仅允许管理归属自己的学员，使用既有精确事件解锁规则；普通账号与游客不得访问。其他管理命令精确指定学习者、Topic、模块、暂停事件。
- 管理员“学员学习情况”页只读查看全站所有学员（`is_student=1`，非管理员）的全部 Topic、题库、错题本、已提交作答历史、活动与正确率统计、已记录用时、全部复习排程及原子卡日期；包含停用账号和受阻项，测试材料统一过滤。使用一致数据库快照，不初始化学员服务、不迁移版本、不改动复习时间；综合复习不重复计数，自主评分单列，旧正文缺失不配新题。口径见 `docs/admin-students.md`。答疑和暂停管理的原归属限制不变。
- 学员头像菜单的“个人学习情况”复用同一报表，只能读取登录身份本人；原子未掌握卡片继续锁定。管理员名称取私密 `admin_name`，学员昵称取 `display_name`，私密备注不得进入任何学员身份、设置、报表或答疑响应。邀请码清单编辑后的同步使用 `scripts/sync_account_names.py`。课程树统一经 `course_hierarchy.project_hierarchy()` 处理隐式课程根、真实章节与直属 Topic。
- 启动入口要验证进程开始时间及实例 ID。后台服务启动不能放入会在正常退出时清理整个 Job 的测试超时器。
- 已获公开发布及更名授权；源码仓库为私有 GraspMemoEdu，独立公开仓库 GraspMemoEdu-Pages 仅发布 web 静态资产和仅含公开 API 地址的 deployment.json。本机源码、数据、缓存和产物目录统一使用 GraspMemoEdu，Python 模块使用 graspmemoedu，专用环境变量使用 GRASPMEMOEDU_ 前缀。前端不提供连接配置；访客通过公共 HTTPS API 自动进入游客模式。修改 web 后先更新整套资源版本号再导出；发布后核对实际资产哈希及真实浏览器加载效果，流程见 README。
- 已授权完整邀请码账号搭建与测试：access_* 表与学习状态同库，凭据仅存哈希；每浏览器游客独立，账号按 Topic 和功能授权。游客可访问全部六科及其中未标记为测试材料的已发布 Topic、指南，仍禁止主动暂停，原自动暂停规则不变；账号的明确 Topic 权限保持原授权。测试用途账号包含原五名普通非学员、两名高阶测试学员及两名配对测试管理员；先做独立验收，再仅使用 test 账号公网验收。原始邀请码只保存在本机私密管理目录，不能写入源码、回执或公开资产。
- 管理正式账号、权限与内容须通过 SSH 在服务器运行 CLI；本机导入只用于准备来源包与隔离验收，不直接更新正式网站。网页不提供建号、权限修改接口；管理员通过“答疑请求”页浏览自己负责学员的请求，并通过“暂停管理”页按精确事件解除这些学员的暂停，当前所有学员默认对应 1 号管理员，后端必须过滤记录、总数、分页及解除操作。每个身份按 learner_id 隔离，状态和提交结果都须核对归属。
- 账号默认动态开放全部当前及后续 Topic、功能；明确权限列表按 Topic/功能两个维度分别覆盖，空列表禁止该维度。`account-permissions --default-topics --default-features` 恢复继承。游客限制不变；旧无模式记录保守保留，已确认默认的正式初始批次按建号记录迁移。
- 先前清空记录任务已完成；后续更新默认保留正式进度，迁移前备份并核对原行。不得将验收进度灌入正式学习者。
- Cloudflare Quick Tunnel 路线已在 2026-09-21 云端迁移时停用，仅保留历史恢复工具；不得用 Start-PublicLearning.ps1 覆盖当前云端入口。公网游客隔离验收使用独立数据目录，正式记录保留。
- 网站已获授权接通 `ReviewBackend(learning_service)`：新完成 Lesson 模块进入 FSRS，到期以“复习”推送；每轮只抽取对应 Practice，复用共享判题和队列规则。既有完成记录不自动回填。评分规则集中在 `practice.py` 并随轮次保存版本；保留度、参数优化、重排和解锁只通过 SSH 在服务器管理。验证必须使用独立数据目录。复习第 3 次错误暂停，Lesson 第 4 次错误暂停。
- 用户已于 2026-09-21 明确授权迁移腾讯云：正式后端为 `https://124.221.144.155`，服务与私密数据见 `docs/tencent-deployment.md`。本机数据只保留作迁移回滚快照；不得重新启动旧维护任务、临时隧道或让本机与云端同时写入。云端已有新进度后，回滚必须先导出并核对云端数据。无明确要求不修改全局规则或 Memory。
