# 同一指南的身份可见性

Guide 正文仍只有一份 Markdown。服务端在渲染前投影正文，网页不会收到当前账号无权查看的块；不是 CSS 隐藏。旧的无标记文档保持原有显示和公开附件兼容。

每个标记必须独占一行，起止名称严格配对；可嵌套。所有外层条件均须满足。支持的名称只有 `student`、`advanced`、`course_hierarchy`、`training_self_rating`、`review_question_mode`、`review_timeout`。未知、不完整、错配或同行混入正文的标记使整份指南返回 `unavailable`，资源也停止提供。代码围栏中的示例标记按文字处理，不影响权限。

```markdown
所有人可见的基础说明。

<!-- guide:student -->
学员答疑说明。
<!-- /guide:student -->

<!-- guide:advanced -->
高阶功能说明。
<!-- guide:course_hierarchy -->
课程层级说明与配图。
<!-- /guide:course_hierarchy -->
<!-- guide:training_self_rating -->
训练自评说明。
<!-- /guide:training_self_rating -->
<!-- guide:review_question_mode -->
复习题型说明。
<!-- /guide:review_question_mode -->
<!-- guide:review_timeout -->
复习超时处理说明（高阶资格即可）。
<!-- /guide:review_timeout -->
<!-- /guide:advanced -->
```

| 当前身份 | 可见块 |
|---|---|
| 已解析的游客身份、管理员账号 | 全部说明（用于了解功能，不授予实际使用权） |
| 普通账号：学员资格且有效 `request_help` | `student` |
| 普通账号：高阶资格且至少一个高阶能力可用（包括超时处理） | `advanced` |
| 普通账号：对应高阶功能有效 | 对应功能块 |
| 普通账号：高阶资格，无需 `review_question_mode` 等功能授权 | `review_timeout` |
| 数学及旧入口的匿名附件请求 | 仅无标记公共部分引用的附件；其他学科维持既有身份要求 |

学员与高阶是独立维度。只有权限名称但没有资格不可见；只有资格但被撤销对应功能时，三个按功能授权的子块同样不可见。复习超时处理遵循 `ReviewPapers._can_mark()`：高阶资格本身即可使用，不依赖功能授权。因此高阶账号即使 `features=[]`，仍看得到 `advanced` 外层和 `review_timeout`，看不到其余三个功能子块。是否为测试用途不在本规则中放宽，既有测试材料整篇限制仍优先执行。

渲染缓存使用最多八个视图的 LRU，按原文 SHA-256 与可见条件组合区分；原文变化会清空旧视图，不同账号共享同一有效投影时复用渲染。返回版本仍绑定投影和有效条件。每次 API 与附件请求重新解析身份，缓存命中也重新选择当前身份的资源集合；权限撤销后，即使携带旧版本 URL，附件也按当前投影重新检查。响应继续 `Cache-Control: no-store`。受限图片需前端使用身份请求头读取后显示，不能把权限放进 URL；不允许匿名请求自动创建游客以取得全部附件。

`GuideReader.read(asset_base, access=None)` 与 `asset(relative, access=None)` 默认只读公共投影，兼容旧调用。可信离线备份如需枚举整份文档附件，应明确传入受信任的管理员 access；不得把这份离线 access 接入网页请求。

验收：`python -m unittest tests.test_guide tests.test_guide_visibility -v`。使用独立临时数据和真实 Node/Marked 渲染，涵盖资格交叉、有效功能、缓存切换、代码围栏、非法标记、附件直接URL、旧版本及权限撤销，不操作正式学习数据。
