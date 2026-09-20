# 作者导入：Markdown → Topic

在后端电脑调用，网页不提供配置或上传入口。第一版把一个已校准的 Markdown 文件导入已有学科、Course；不自动创建 Course，不覆盖既有 Topic，不写账号权限或学习记录。采用默认权限的账号会自动获得新 Topic，设置过 Topic 覆盖的账号继续受其明确配置限制。

## 一条命令

```powershell
pwsh -NoProfile -File 'D:/Projects/MathLearningWeb/Import-Topic.ps1' `
  -Source 'D:/Artifacts/我的物理稿.md' `
  -TopicId 'physics-my-topic' -Subject physics -CourseId test-physics-course
```

成功返回 `status: installed`、Topic 路径和审核包路径。随后加载新目录：

```powershell
pwsh -NoProfile -File 'D:/Projects/MathLearningWeb/Start-PublicLearning.ps1' -RestartBackend
```

账号默认开放全部 Topic 和功能，新增内容自动继承；明确设置的 Topic 或功能列表覆盖各自默认值。物理等非数学学科需要登录。权限管理仍在本机 CLI，见 [账号管理](accounts.md)。

本次物理验收新增 `physics-uniform-speed-import-test`（「【测试】匀速直线运动：速度计算」，1 个 Example、7 道 Practice），放在 `test-physics-course` 下，保留原有测试 Topic。默认权限账号可直接学习该 Topic。

## 输入格式

沿用已有导入格式：两空格缩进的 Markdown 列表，一份文件恰好一个根 Topic、一个 Introduction、连续编号的 Example；每个 Example 与 Practice 各有一个非空 Explanation。每个 Example 至少 7 道 Practice，以满足现行练习补题机制。题量可因模块而异。

```markdown
- Topic 标题
  - Introduction
    - 概念、共同条件与公式。
  - Example 1
    - 完整题面。
    - Explanation
      - 推导与最终答案。
    - Practice 1
      - 完整题面。
      - Explanation
        - 推导与最终答案。
```

上例仅展示层级；实际文件须继续到 Practice 7 或更多。支持原有 Markdown/LaTeX；主动 HTML、脚本、远程图片等不属于输入合同。字段不齐或题量不足会在调用 API 前失败。不会自动补题、改写源稿或猜测缺失条件。

支持分组生产稿：每个 Example 下共 5 组、每组 3 道，依次标为 `Practice 1.1`、`Practice 1.2`、`Practice 1.3`、`Practice 2.1`，直到 `Practice 5.3`。所有 Practice 仍是 Example 的直接子节点，不增加分组容器。每个 Example 的分组编号从 1.1 重新开始；同一 Example 内不混用整数与分组编号，重复、缺号、乱序或不完整分组均拒绝导入。

导入保持源顺序和正文不变，将分组编号映射到普通连续编号：1.1→1、1.3→3、2.1→4、5.3→15。网站题目标题为 `Practice 1` 到 `Practice 15`，题目 ID 继续使用 `example-N-practice-M`。原始分组编号存入题目的 `source_practice` 元数据，包含 `label`、`group`、`member`，不显示在学生题面中。原有连续整数编号输入仍兼容，已有 Topic 和学习抽题机制不因这项导入支持改变。

## 标准流程与字段

1. 保存原始文件快照，脚本解析结构并生成稳定题目 ID、源码行号和题面哈希。
2. LLM 按模块结合 Introduction 提取 `reference_answer`、`answer_type`、`constraints`。
3. 第二次独立 API 调用重新核对答案、解析、条件、单位与精度，逐题记录理由。任何拒绝都会停止；传输或响应格式异常最多尝试 3 次，保留失败记录。
4. 脚本按题目 ID 精确注入字段，渲染 Introduction、题面和解析；校验题目覆盖、哈希与最少练习数量。原有精确算术验证入口仍然保留；本流程的通用语义校验明确标记为 `llm-reviewed`，不声称数学形式证明。
5. 安装时重新运行转换并逐字段比较结果，先写永久内容，再原子切换目录。保留切换前目录快照；冲突、篡改和重复 ID 均不会覆盖旧内容。安装锁防止两个导入器同时写目录。

标题、Introduction、模块、题面、解析来自源文件；答案类型、标准答案和判题约束来自经过独立审核的答案侧车。源文件、答案侧车、审核报告和 Topic 都以 SHA-256 关联。网站备份同时携带这些来源和审核材料，恢复时重新核验。

## 分阶段调用

在项目目录运行；`--data-dir` 放在子命令之前。

```powershell
$python = 'D:/AppData/MathLearningWeb/venv/Scripts/python.exe'
& $python -m math_learning author-prepare --source 'D:/Artifacts/我的物理稿.md' --topic-id physics-my-topic --run-dir 'D:/Artifacts/MathLearningWeb/author-import/runs/my-new-run'
& $python -m math_learning author-install --package 'D:/Artifacts/MathLearningWeb/author-import/runs/my-new-run' --subject physics --course test-physics-course
```

`author-prepare` 只产生审核包；`author-install` 不再次调用 LLM。`author-import` 合并两步。Course 有多个显式 Unit 时须提供 `--unit`（PowerShell 为 `-Unit`）。每次 prepare 使用新运行目录；失败后修订源稿或通用流程，再从源文件重新执行，不能修改审核 JSON 强行通过。相同包重复 install 返回 `already_installed`。

默认运行材料位于 `D:/Artifacts/MathLearningWeb/author-import/runs`；已入库内容位于数据目录的 `imported-topics/<TopicId>`。API 请求、响应、逐题理由和 `manifest.json` 留在本机，不能加入源码仓库。返回失败状态时进程退出码非零。未激活的残留目录或锁须核对原运行进程后处理，不自动删除。

## API 与扩展边界

复用本机 `ai-production-supervisor` 的配置发现和模型规范化。专用凭据为 `DEEPSEEK_CODEX_API_KEY`（进程或 Windows 用户环境），仅发送到 DeepSeek 官方 HTTPS 地址，禁止重定向。可用 `AUTHOR_IMPORT_MODEL` 指定模型；当前默认 `deepseek-flash`，关闭思考。缺少凭据时明确失败，不回退其他项目的密钥。完整教学稿及答案会提交该 API 审核。

准备包逻辑在 `math_learning/author_import.py`，安装在 `math_learning/topic_install.py`，渲染沿用 `scripts/import_course.mjs`。后续新增输入格式或内容类型时在这些边界扩展，不改变既有学习/复习状态机。当前不支持自动更新已有 Topic、插图附件导入或任意 Markdown 排版。
