# MathLearningWeb

- 本机数学学习应用；Lesson 产品行为以 `D:/Artifacts/MathLearningWebPlan/01-需求与方案.md`、`04-推送系统补充需求.md` 为准，复习后端以 `docs/review-backend.md` 为准，当前实现/运维以 README 为准。
- 主页布局以 `D:/Artifacts/MathTopicPipeline/MathAcademy-learn-主页复刻需求.md` 为准；Course「再探小学算术」组织 Topic「数字化为字母」，不改变其 ID、版本和既有进度。网站主页当前只产生真实 Lesson 任务，尚未接入 XP、测验或复习展示。
- GUIDE 由 `D:/AppData/MathLearningWeb/guide/guide.md` 与相对 assets 单向发布；内容分极简使用流程和系统工作原理，配图用本网站真实截图标注。更新指南不能夹带技术配置说明或暴露其他本机文件。
- 技术栈：Python/FastAPI/SQLite，原生 HTML/CSS/JS，离线 MathJax SVG；复用 `D:/Projects/MathAnswerJudge`，不在网页判题。
- 启动：`pwsh -NoProfile -File D:/Projects/MathLearningWeb/Start-Learning.ps1`；默认 127.0.0.1:8767。8765 属于 AnkiConnect，不要终止。
- 源码仅在本仓库；数据在 `D:/AppData/MathLearningWeb`，证据在 `D:/Artifacts/MathLearningWeb`。课程正文、答案表、数据库、配对信息和密钥不进入 Git。
- 修改后运行 `D:/AppData/MathLearningWeb/venv/Scripts/python.exe -m unittest discover -s tests -v`。实际学习流程须用独立验收目录和真实判题核验，不能消耗正式进度。
- 课程导入从只读原稿重跑；排版勘误放外置 sidecar 并保留来源。不要手改生成课程冒充管线通过。
- 课程版本不匹配须保留旧进度；暂停与评分由 SQLite 事务决定；在途判题不持有数据库锁，旧轮次结果不能推进新轮次。
- 后端只绑定回环地址；禁止 Web 解锁。管理命令精确指定学习者、Topic、模块、暂停事件。
- 启动入口要验证进程开始时间及实例 ID。后台服务启动不能放入会在正常退出时清理整个 Job 的测试超时器。
- 已获公开发布授权；独立公开仓库 MathLearningWeb-Pages 仅发布 web 静态资产和仅含公开 API 地址的 deployment.json，源码主仓库保持私有。前端不提供连接配置；访客通过公共 HTTPS API 自动进入游客模式。发布后核对实际资产哈希。
- 已授权完整邀请码账号搭建与测试：access_* 表与学习状态同库，凭据仅存哈希；每浏览器游客独立，账号按 Topic 和功能授权。游客仅禁止主动暂停，原自动暂停规则不变。初始 15 个账号中 10 个正式使用、5 个固定 test 用途；先做独立验收，再仅使用 test 账号公网验收。原始邀请码只保存在本机私密管理目录，不能写入源码、回执或公开资产。
- 管理账号和权限使用本机CLI；网页不得暴露管理员接口。每个身份按 learner_id 隔离，状态和提交结果都须核对归属。
- 先前清空记录任务已完成；后续更新默认保留正式进度，迁移前备份并核对原行。不得将验收进度灌入正式学习者。
- 用户已授权免费公网测试、本机持续作为后端。Start-PublicLearning.ps1 管理 Cloudflare Quick Tunnel 并更新固定网页地址；不得暴露其他本机服务，不开路由器端口。公网游客隔离验收使用独立数据目录，正式记录保留。
- FSRS 复习后端是显式构造 `ReviewBackend(learning_service)` 后才启用的 Python 能力，当前网站未接线、未启用复习入口、未回填正式历史。评分按 Example 模块结算，保留度、参数优化与重排由后端管理接口控制；验证必须使用独立数据目录。复习错误达到 3 次时暂停，Lesson 原有第 4 次错误暂停规则保持原样。
- 不自行把复习后端接到正式网站或迁移云服务器。无明确要求不修改全局规则或 Memory。
