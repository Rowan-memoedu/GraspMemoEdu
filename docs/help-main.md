# 全站答疑与主对话

答疑统一使用 `#/qa`；旧学科路径与旧答疑请求路径会转到此页面。学员的置顶主对话不附带 Topic、内容块或反馈选项，下面保留历次按内容块提交的答疑与质量反馈。管理员选择自己负责的学员，同时查看该学员的主对话和全部学科答疑。没有提交记录的学员也可使用主对话。

主对话按 `learner_id` 保持唯一，管理员重新分配后继续保留历史；查询、发送、编辑和已读均由服务端校验本人身份或当前管理员归属。管理员仅能修改自己的消息，学员也仅能修改自己的消息。新接口为 `/api/help-main/messages`、`/api/help-main/read`；`/api/help-center` 可用 `learner_id` 精确筛选学员，通知摘要仍从不带学科或学员参数的接口读取。四个新增 `help_main_*` 表仅存聊天消息、历史版本、事件和已读位置，不初始化学习或 FSRS。

双方消息和最初问题使用 Marked、DOMPurify、KaTeX 显示 Markdown、`$...$` / `$$...$$` / `\(...\)` / `\[...\]`。原始消息仍保留在数据库；代码段中的公式不渲染。原始 HTML 显示为文本，脚本、事件处理器及危险链接不可执行，公式不允许信任命令或共享宏。正文照片等已有受控内容块维持原来的展示。

输入框采用 CodeMirror 6，保留发送、引用回复、修改、冲突检查及草稿机制；提供加粗、斜体、列表、公式和预览。Tab / Shift+Tab 缩进，支持撤销；Esc 后按 Tab 可离开编辑器。最长仍为 4000 字符。

所有对话编辑框支持粘贴或拖入图片，一次最多 10 张；加入后自动预览，消息可以仅含图片。浏览器将常见图片转成最大边长 2048 的 PNG，单张上传上限 2 MB。`/api/help-images` 沿用身份与当前对话授权，不提供公开图片 URL；消息仅保存 `![](help-image:<id>)` 引用。后端用 Pillow 解码并重新编码，丢弃元数据与额外负载；跨对话引用拒绝提交。图片在私密数据目录 `help-images/` 持久保存，备份根据同一数据库快照逐个绑定哈希，恢复验证图片与数据库完全对应，旧备份仍兼容。修改消息或取消草稿不删除图片，以保留消息历史版本。

依赖固定在 `scripts/chat-dependencies/package-lock.json`。在独立缓存目录安装锁定依赖后，用 `GRASPMEMOEDU_CHAT_RUNTIME` 指定目录并执行 `node scripts/build_chat_editor.mjs`。生成的 `web/vendor/chat` 不依赖 CDN；发布脚本核对 `scripts/chat-assets.json` 的完整清单与哈希，许可证随资源发布。

参考：[CodeMirror Tab](https://codemirror.net/examples/tab/)、[Marked](https://marked.js.org/)、[KaTeX 配置](https://katex.org/docs/options.html)。
