# 网站临时下线与恢复

用户已要求准备一句话执行的可逆操作。说“网站下线”时执行 `offline`；说“恢复上线”时执行 `online`。本次准备不下线网站，实际开关在明确收到对应指令后执行，不额外要求二次批准。

```powershell
& D:/AppData/GraspMemoEdu/venv/Scripts/python.exe D:/Projects/GraspMemoEdu/scripts/site_availability.py offline
& D:/AppData/GraspMemoEdu/venv/Scripts/python.exe D:/Projects/GraspMemoEdu/scripts/site_availability.py online
```

`status` 只读检查；`verify` 核验当前目标状态；`prepare` 在网站在线时保存配置，不可覆盖待恢复的下线快照。命令按 Windows 进度超时规则运行，传播等待时持续输出状态。达到等待上限保留 pending 状态，不能宣称完成，继续核验即可。

## 操作范围

- 只处理公开静态仓库 `Rowan-memoedu/GraspMemoEdu-Pages` 的 Pages 设置与发布，不删除仓库或更改分支、文件、可见性。
- 下线前保存 Pages 的 main 分支、根路径、HTTPS 设置、当前提交和公网监测的启用状态至 `D:/AppData/GraspMemoEduOperations/availability.json`。实际第一次下线前重新读取，避免使用准备时的过期设置。恢复前验证发布分支没有被其他操作修改，不用 Git reset 覆盖变更。
- 暂停本网站的 GitHub 公网监测工作流，防止主动下线产生故障通知；恢复时仅恢复此前启用的监测。不改变其他监测、服务器健康检查、备份和证书续期。
- nginx 只在已有 GraspMemoEdu 的 `/api/`、`/guide-assets/`、`/subject-guide-assets/` 代理位置包含 `deploy/graspmemoedu-access.conf`。创建固定标志文件 `/etc/nginx/graspmemoedu.offline` 后这些请求返回 404；删除该标志后恢复代理，不需要重启后端或 reload nginx。`/api/health` 始终保留，其他网站的路由不变。
- 下线通过 GitHub 官方 Pages DELETE 接口关闭发布配置，保留源码；恢复通过 POST/PUT 重建已保存配置并触发构建。日常 `Publish-Frontend.ps1` 对已关闭的 Pages 拒绝发布，避免误上线。
- 不写课程、题库、账号或学习数据库，不重置进度，不停止后端。开关前已接受的在途请求可以正常结束，不取消判题任务。

## 验证与边界

实际准备在隔离 loopback nginx 上验证了在线、404、恢复和健康/其他路由保留，正式站点一直在线。控制脚本的失败恢复、重复执行、分支变化和原本已停用监测的保留通过回归验证。正式 GitHub Pages 的下线/恢复没有演练，因为用户要求准备而未要求实际下线；首次执行时以公网 404/200 和 Pages 设置回读验收，不能把 API 调用成功当成站点已经下线。

GitHub Pages 与浏览器缓存可能延迟呈现，已打开网页中的已加载内容不会远程消失；新的内容读取和学习请求会被后端开关挡住。部署前恢复配置保存在 `/var/backups/graspmemoedu-access/nginx-before-access-switch.conf`；不要用该整份历史文件覆盖后来其他网站的 nginx 更改，正常恢复只移除标志文件。

参考：[GitHub Pages 官方 API](https://docs.github.com/en/rest/pages/pages)、[nginx rewrite 指令](https://nginx.org/en/docs/http/ngx_http_rewrite_module.html)。
