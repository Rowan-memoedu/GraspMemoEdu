# Hyperskill Topic 接入

每篇 theory 对应一个统一架构的 Topic v2。译文按最外层正文标题拆成有序的多个 Introduction；首个标题前非空内容命名为“引言”，没有内容则不创建空引言。低一级标题留在所在节内。Topic 的 `modules`、`memory_chunks`、`memory_points`、`flashcards` 都为空，当前为纯引论；以后可按统一 Topic 规则在对应 Introduction 下增加范式训练或原子练习，空结构不产生 FSRS 卡。

Practice 独立进入原生 `TrainingCatalog` 题库。课程 ID 保持 `hyperskill`，题库 ID 为 `hyperskill-practice`，显示名称均为 Hyperskill。题库沿原来源路径建立父子题组，再以源 Topic 建立叶题组，一道 problem 对应一道题及一张独立 FSRS 卡。Topic 原链接仍打开正文，不重定向到题库；题目仅保存 `hyperskill` 来源绑定，不把答案伪装成普通本地参考答案。

`prepare_hyperskill.mjs` 从本地译文和已认证读取的 practice 生成确定性来源包，`prepare_hyperskill_branch.mjs` 按源目录合并完整分支。HTML 使用安全渲染器的 HTML 入口，避免 Markdown 解析改变代码里的星号和下划线。图片复用 Hyperskill→RemNote 的下载、哈希与 SVG 转 PNG 路径，优先复用本地已校验图片；原附件和来源 JSON 随包保存，网页内嵌图片。`academy_import --extend-course` 对新格式调用 `hyperskill_import`：追加新 Topic 和题组，已有问题及父子关系不能被覆盖，重复安装幂等。题库追加内容时仅迁移版本绑定，已存在的卡片、日期、作答快照和评分保持原值。

旧原子 Topic 的一次性转换在停服发布窗口完成。原 `topics` 行逐字保存在 `hyperskill_topic_archives`，历史、在途轮次和 FSRS 卡移到题库，正文使用新的阅读状态；旧事件与原始内容包保留。新章节不凭旧练习状态伪造“已阅读”。迁移前备份，异常同时回滚数据库、题库和正文指针；未处理的暂停、判题中状态、部分显式 Topic 权限或已排入试卷的题库卡片会阻止迁移，不能静默扩大访问权。测试标记随正文激活，题库同样仅向合格测试账号开放。

当前支持 choice、matching、number、string、fill-blanks、sorting、parsons、table、code。填空保留题内空格，排序保留选项身份，Parsons 同时提交行顺序与缩进，表格按实际行列名称提交。代码题保留源模板和公开输入输出示例，语言以 Hyperskill 返回值为准。未知题型停止导入，不能静默删题。

共享阅读页按源标题等级构建可折叠正文层级，题目身份不随展开收起变化；代码字符、缩进、列表编号和图片出现顺序保留。代码块可横向滚动，正文适应窄屏。代码题支持网页编辑或打开已安装的 IDEA；本机每轮只创建一个独立代码文件，再次打开保留编辑结果。IDEA 保存后，通过网页“读取 IDEA 已保存代码”取回，再点击提交。该入口只编辑代码，最终判题仍由 Hyperskill 完成；既有 Academy 题库插件流程保留。

网页使用共享 `training.js` 阅读器、`hyperskill.js` 输入控件与 Academy 的轮次授权。旧原子格式保留兼容入口。题目需要动态选项时，通过 127.0.0.1:18769 的本机服务取得当前 attempt dataset。服务绑定固定 HTTPS 后端与明确的网页 Origin，不接受任意代理 URL、命令或路径。

本机服务从现有 LiveCookieSnapshot 读取 Hyperskill 登录态，每次写入前检查账号身份。Cookie 和 CSRF 凭据只用于 Hyperskill 请求，不传到 GraspMemoEdu 或网页。网页的能力票据限制到当前账号、内容版本和作答轮次。运行目录与配置位于本机私密应用数据目录，不能提交 Git。

提交前持久化请求；POST 返回不确定时只查询原提交，不自动再发 POST。查询以本人和题目限定，再按 attempt、答案和时间关联；Hyperskill 当前忽略列表中的 attempt 查询参数，不能依赖它过滤。匹配不唯一时保留待确认状态。判题完成后，原始提交 ID、attempt ID、答案、判定、时间及反馈保留在 `training_external_rounds` 回执；云端确认前保留本机待传记录。同一学员的上游提交不能被重用于另一轮评分。

复习准入使用全学科共用题库规则：首次直接通过且未主动加入错题本，记 5、保留作答记录，不创建 FSRS Card，也不进入后续复习。主动加入错题本或已有排程的题目，直接通过 Good；少量有效错误后通过 Hard；三次不同有效错误后通过或主动结束失败轮次 Again。对相同答案的重复失败不重复增加错误负担；没有终态判定不评分。需要复习的每个 problem 使用独立 FSRS Card，完成一道题不会把整个 Topic 标记完成。

此接口集成使用网站内部协议，协议变化时应停止提交并修复适配器。判题状态不等于完整标准答案：界面展示服务实际返回的判定和反馈，不承诺每道题都会返回完整解答。

本机启动使用 `Start-HyperskillBridge.ps1 -ConfigPath <private config>`。配置包括固定 backend、origins、port、directory、cookieModule、cookieWorkflowRoot、cookieDataRoot、expectedUserId，不含复制出来的 Cookie。服务只监听本机地址；浏览器可能要求用户允许本地网络访问。此次不设置开机自启。

验证入口：`tests/test_hyperskill_training.py`、`tests/test_hyperskill.py`、`scripts/test_hyperskill_bridge.mjs`、`scripts/test_hyperskill_sections.mjs`、`scripts/validate_hyperskill_branch.py`、`scripts/hyperskill_bank_acceptance.mjs`、`scripts/test_hyperskill_render.mjs`。经用户授权的真实提交验收使用测试学员，保留上游提交 ID、独立卡片的复习日期和去重结果；合成契约测试不作为真实提交通过的证据。
