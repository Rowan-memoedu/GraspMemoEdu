# MathLearningWeb

- 本机数学学习应用；产品行为以 `D:/Artifacts/MathLearningWebPlan/01-需求与方案.md`、`04-推送系统补充需求.md` 为准，当前实现/运维以 README 为准。
- 技术栈：Python/FastAPI/SQLite，原生 HTML/CSS/JS，离线 MathJax SVG；复用 `D:/Projects/MathAnswerJudge`，不在网页判题。
- 启动：`pwsh -NoProfile -File D:/Projects/MathLearningWeb/Start-Learning.ps1`；默认 127.0.0.1:8767。8765 属于 AnkiConnect，不要终止。
- 源码仅在本仓库；数据在 `D:/AppData/MathLearningWeb`，证据在 `D:/Artifacts/MathLearningWeb`。课程正文、答案表、数据库、配对信息和密钥不进入 Git。
- 修改后运行 `D:/AppData/MathLearningWeb/venv/Scripts/python.exe -m unittest discover -s tests -v`。实际学习流程须用独立验收目录和真实判题核验，不能消耗正式进度。
- 课程导入从只读原稿重跑；排版勘误放外置 sidecar 并保留来源。不要手改生成课程冒充管线通过。
- 课程版本不匹配须保留旧进度；暂停与评分由 SQLite 事务决定；在途判题不持有数据库锁，旧轮次结果不能推进新轮次。
- 后端只绑定回环地址；禁止 Web 解锁。管理命令精确指定学习者、Topic、模块、暂停事件。
- 启动入口要验证进程开始时间及实例 ID。后台服务启动不能放入会在正常退出时清理整个 Job 的测试超时器。
- 已获公开发布授权；独立公开仓库 MathLearningWeb-Pages 仅发布 web 静态资产，源码主仓库保持私有。真实 HTTPS→本机 Chrome 通过正常本地网络权限验收，发布后核对实际资产哈希。
- 已授权邀请码账号及游客框架：access_* 表与学习状态同库，凭据仅存哈希；每浏览器游客独立，账号按 Topic 和功能授权。游客仅禁止主动暂停，原自动暂停规则不变。本轮不测试账号登录使用，只验收游客。
- 管理账号和权限使用本机CLI；网页不得暴露管理员接口。每个身份按 learner_id 隔离，状态和提交结果都须核对归属。
- 用户已授权清空原学习记录，清空前完整备份与原数据库在 D 盘保留；不得将验收进度灌入正式学习者。
- 不扩展到复习调度、云后端或公网学生访问。无明确要求不修改全局规则或 Memory。
