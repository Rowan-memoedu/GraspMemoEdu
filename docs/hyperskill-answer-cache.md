# Hyperskill 导入后的答案缓存

`HyperskillRemNoteWorkflow/dual_workflow.py` 在原网站导入分支成功后调用独立脚本 `scripts/hyperskill_answer_cache.py`。原翻译、RemNote 导入、网站题面采集、内容发布及语义判题保持原流程。导入已完成时也会调用后置脚本，便于单独恢复缓存缺口。

每次从正式服务器全部 `training-banks` 读取 Hyperskill 题，按现有判题器的题目内容键去重，跳过代码题和已有正确答案缓存的题。题面改变后会形成新键，旧答案不会套用到新题。正常运行没有题数上限；`--canary` 只用于显式的每类一题验收。

```powershell
& D:\AppData\GraspMemoEdu\venv\Scripts\python.exe D:\Projects\GraspMemoEdu\scripts\hyperskill_answer_cache.py plan
& D:\AppData\GraspMemoEdu\venv\Scripts\python.exe D:\Projects\GraspMemoEdu\scripts\hyperskill_answer_cache.py run
```

流程先查询已配置账号的官方正确提交。缺失答案通过现有 `ai-production-supervisor` 的 Codex Exec 传输生成候选；只发送题面、公开数据、必要图片和先前错误反馈。模型不能读取凭据或调用工具，也不直接决定缓存内容。错误候选按官方反馈重新生成，已成功题目不重做。

题目依赖的公开文本、CSV 或 ZIP 附件由后置流程读取并保留原文件和哈希。数字序列保留原顺序，并计算通用统计；ZIP 只读取目录元数据，不解压或执行其中内容。完整目录清单保存到运行证据，较大的清单向模型提供覆盖全部条目的目录统计，并明确标记未放入模型上下文的原始列表。正式缓存仍以官方正确回执为准。

`hyperskill_cache_submit.mjs` 复用原 Bridge 的选项重排和提交断点保护，Cookie 只由已有本地快照模块读取。每次官方 POST 前核对账号；401、403、429 或身份变化停止新的请求。状态不明时查询原提交，不再次 POST；只有独立确认 `rejected` 才新建尝试，原记录仍保留。

若官方实例抽取的选项与已安装题面不同，先核对官方题干和题型未变，再获取选项集合完全匹配的未作答实例。每题最多保留 60 个不匹配实例，未匹配时不提交；题干改变则保留缺口，不改网站题面，也不把另一版选项的答案移植过来。

答案写入前必须满足：官方状态为 `correct`；再次读取提交和 attempt 核对账号、题号、答案及数据集；通过原 `HyperskillLocalTraining` 的隔离判题。服务器 SSH 管理脚本只允许向 `hyperskill_answers` 执行 INSERT，并回读新行及全部旧行。网站学习记录、FSRS、账号和语义判题不参与写入。

逐题候选和提交断点在 `D:/AppData/GraspMemoEduHyperskillCache`，运行证据在 `D:/Artifacts/GraspMemoEdu/hyperskill-answer-cache`。全局文件锁防止两个后置任务同时提交。每批成功后立即缓存，失败不撤销其他已验证答案；相同命令重跑会重新扫描正式缺口。官方提交次数和反馈保存在逐题记录与 `requests.jsonl` 中。

最后重新扫描正式题库：`pending=0` 且无缺题面、未支持题型等阻塞才报告 `complete`。模型生产失败、六轮后仍未取得正确答案或官方接口不可用都会保留未完成状态和断点。

服务器需安装同仓库的 `scripts/hyperskill_answer_cache_admin.py`，使用现有 SSH 身份和 `/opt/graspmemoedu/venv/bin/python`。该管理脚本不提供公网 API，也不需要重启网站。
