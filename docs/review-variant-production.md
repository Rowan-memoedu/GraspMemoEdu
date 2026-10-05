# 数学题库复习的同组变式补充

只接独立数学题库的复习点，课程 Lesson、Topic 的 Example → 5×3 Practice 生产和其他学科均不接入。
原题视为已有 Practice，沿用原生产工作流的第二步：保持任务、对象形式、判断标准、核心运算和解析骨架，
只改变局部数据，每批生成两个同组 Practice。保留题库当前的题型（包括多选），不套用 15 题门槛。

`variant_worker` 独立进程每 30 秒查看需求：至少有一位仍可学习该题、未停止复习的非管理员，
其该复习点的未见变式数为零，才生产两题。未见数量为一时不补充。已有变式全站共用，
已见记录 `review_card_uses` 仍按学习者隔离；打开复习即视为已见，原题完成首答后也视为已见。
不预生成整本题库，不修改 FSRS、原题版本、课程或正式进度。管理员内存预览不会触发生产。

一次生成调用 + 一次独立 AI 审核。审核通过、交互结构校验及现有离线 Markdown/MathJax 编译通过后，
经 `attach_training_variant` 在同一事务内安装两题。模型不能提交 HTML；原图可原样复用，图中数据不得擅改。
再次核对源题、权限和耗尽条件后才入库。生成期间不持有数据库锁，也不阻塞网页请求。

生产阶段、来源快照、两次 API 结果和用量、审核、编译结果保存在私密 SQLite 的 `review_variant_jobs`，
随现有网站备份一并保存。每个原题版本和已有变式集合对应唯一任务，多进程以状态条件更新竞争任务。
拒绝或失败不入库，也不在轮询中反复调用。中断的 API 调用在 30 分钟后记录为结果未知；
已完成的生成或审核可继续下一步，不重复已完成的 API 调用。

## 配置与运行

专用变量：`GRASPMEMOEDU_VARIANT_API_KEY`、`GRASPMEMOEDU_VARIANT_BASE_URL`（`https://api.deepseek.com`）、
`GRASPMEMOEDU_VARIANT_MODEL`（`deepseek-flash`）、`GRASPMEMOEDU_VARIANT_REASONING_EFFORT`（`low`）。
Windows 优先当前进程，再读用户环境变量；不借用导入、判题或本地生产的 Key。
服务器保存于仅 root 可读的 `/etc/graspmemoedu/variants.env`，由独立 systemd 服务注入。

部署 `deploy/graspmemoedu-variants.service`，复用网站既有 Node 22、marked 和 mathjax-full 运行时。
密钥、题目和模型响应不进入 Git、公开前端或日志。服务日志只含任务 ID、状态及简短错误。

```sh
python -m graspmemoedu.variant_worker --data-dir /var/lib/graspmemoedu --status
python -m graspmemoedu.variant_worker --data-dir /var/lib/graspmemoedu --watch
systemctl status graspmemoedu-variants
journalctl -u graspmemoedu-variants --since today
```

禁用补题服务即可停止后续生产；已安装变式及原有复习照常可用。失败先查看私密任务记录，
修正通用流程再重跑隔离样本，不直接修改候选题或降低审核标准。
