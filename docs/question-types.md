# 题型与判题

Example 与 Practice 共用可选 `interaction`（version 1）。不填时保留原问答题，继续交给已有 LLM 判题。`answer_type` 仍是原答案描述字段，不作为前端控件类型。

在作者 Markdown 的每题下，与 `Explanation` 并列增加一个 `Interaction` 节点；它只能包含一份 JSON 对象。该节点不显示在题干或解析里。原 `Explanation` 仍须保留，作为教学解析和旧判题参考。

```text
- 示例 Topic
  - Introduction
    - 学习说明。
  - Example 1
    - 太阳从 {{blank:east}} 方升起。
    - Interaction
      - {"version":1,"type":"fill_blank","grading":"exact","blanks":[{"id":"east","answers":["东"]}]}
    - Explanation
      - 太阳东升西落。
    - Practice 1
      - ……
```

原每个 Example 至少 7 道 Practice 的结构约束不变。导入、来源包、安装都复用既有机械校验，不调用 LLM 生成题型或答案。

## 问答与填空的两种判题模式

`grading: "exact"` 对应复刻，服务端脚本严格比较；`grading: "semantic"` 对应复现，调用已有独立判题 LLM。选择、判断与配对始终由脚本判题。

复刻只合并 Unicode NFC、全角 ASCII 与空白差异；保留大小写、正负号、小数点、数学符号及上标差异，不做词义或代数等价推断。可用 `answers` 列出允许的精确写法。需要包装格式时设置 `extract_pattern`：正则必须恰有一个捕获组，完整匹配作答后提取该组比较，执行有超时保护。不要设置允许任意前后文字的宽泛模式。

```json
{"version":1,"type":"text","grading":"exact","answers":["42"],"extract_pattern":"(?:答案[:：])?\\s*([0-9 ]+)\\s*"}
```

```json
{"version":1,"type":"text","grading":"semantic","answers":["水由液态变成固态"]}
```

语义问答可省略 `answers`，此时沿用 `reference_answer`。语义填空在一条判题请求中提供题干、每空参考、所有作答以及“所有空都正确才判整题正确”的要求。

## 单项选择题

```json
{"version":1,"type":"choice","options":[{"id":"oa","text":"$3$"},{"id":"ob","text":"$4$"},{"id":"oc","text":"$5$"},{"id":"od","text":"$6$"},{"id":"oe","text":"$7$"},{"id":"of","text":"$8$"}],"correct_id":"ob"}
```

通用结构支持 2–12 个选项、唯一正确项；课程生成式转换默认六选一，书籍机械清理按原选项数量。ID 必须唯一，不能携带正确性含义。提交为 `{"selected":"ob"}`，空选、多选、未知 ID 和额外字段均拒绝，不计错。公共元数据不含 `correct_id`。

选项文本复用安全 Markdown 和离线 MathJax。Lesson 与 Review 按呈现标识确定选项顺序，刷新、恢复草稿和重试保持不变；后续呈现重新排列。鼠标、触屏和方向键均可选择，Ctrl + Enter 提交。转换旧题与保留旧记录的版本规则见 [题库转换工作流](question-bank-workflow.md)。

可选 `shuffle:false` 保持原选项顺序，适用于原书解析按 A/B/C/D 指代的题目；省略时沿用原随机顺序。

## 多项选择题

```json
{"version":1,"type":"choice","multiple":true,"shuffle":false,"options":[{"id":"oa","text":"甲"},{"id":"ob","text":"乙"},{"id":"oc","text":"丙"}],"correct_ids":["oa","oc"]}
```

复用 choice 控件并显示复选框。提交 `{"selected":["oc","oa"]}`，集合完全一致才判整题正确；少选、多选、错选均判错。空数组、重复或未知 ID 视为无效作答。原单选 schema 与字符串提交保持兼容。`correct_ids` 留在服务端，不进入公共元数据；单选和多选均由脚本判分。

## 判断题

```json
{"version":1,"type":"true_false","statements":[{"id":"a","text":"$2+2=4$","correct":true},{"id":"b","text":"$3+3=7$","correct":false}]}
```

1–20 个判断项。每项使用真正的 JSON 布尔值。前端呈现逐项正确/错误选项；服务端记录逐项反馈。提交 JSON 形如 `{"a":true,"b":false}`。

## 填空题

数学等价式或答案不唯一的填空使用 `grading:"semantic"`。判题同时收到按空位整理的参考表达和原参考答案/解析；清洗阶段不重新解题。

```json
{"version":1,"type":"fill_blank","grading":"exact","blanks":[{"id":"a","label":"升起方向","answers":["东"]},{"id":"b","label":"落下方向","answers":["西"]}]}
```

题干写 `太阳从 {{blank:a}} 方升起，从 {{blank:b}} 方落下。`。每空标记必须恰好出现一次，不能放在 `$...$`/`$$...$$` 内。1–20 空，按题干位置显示输入框；`label` 可选。复刻填空可逐空设置 `extract_pattern`。提交 JSON 形如 `{"a":"东","b":"西"}`。

## 配对题

```json
{"version":1,"type":"matching","direction":"both","fixed_side":"left","left":[{"id":"l2","text":"$2$"},{"id":"l3","text":"$3$"}],"right":[{"id":"r9","text":"$9$"},{"id":"rx","text":"干扰项"},{"id":"r4","text":"$4$"}],"pairs":[{"left":"l2","right":"r4"},{"left":"l3","right":"r9"}]}
```

- `pairs` 定义 1–20 个框与服务端正确关系；两侧选项各不超过 40 个，每侧数目均不得少于框数。
- 每侧 ID 唯一，正确关系一对一。每次作答中每个选项只能用一次。
- `direction` 为 `forward`（默认单向）或 `both`（双向）。箭头表达题意；判题仍按给定关系。
- `fixed_side` 可为 `left`、`right` 或省略/null。预填侧从标准关系取得框内初始内容并锁定，另一侧保留全部候选与干扰项。
- 两侧都可编辑时，整行交换不影响正确性。候选区与框均支持拖动，另有点击选项再点击框、上下移动按钮，触屏不依赖拖动。
- 已占用的框拖到另一框会交换该侧内容，拖回候选区或直接点击该框会移除。另一侧不随之移动。
- 判断项和配对选项的 `text` 沿用作者 Markdown 安全渲染、相对 PNG 与离线 MathJax；导入生成 `html`。不要手写 HTML 或引入外部脚本。

提交形如 `[{"left":"l3","right":"r9"},{"left":"l2","right":"r4"}]`。标准关系、布尔答案、精确答案与正则留在服务端；公共元数据只含显示信息和明确预填的那一侧。

## 状态兼容与验收

结构化答案序列化成 JSON 字符串，继续使用原 2000 字作答上限、草稿键、尝试 ID、版本检查、幂等提交和重试机制。空项、重复选项、未知 ID、修改固定侧都会在服务端拒绝，不计为普通错题。总长度上限包括 JSON 结构，请控制项数和答案长度。

标准参考本身应是能直接填写的答案；如果正则强制包装且无法接受裸参考，游客浏览模式不会猜测包装。此题仍可正常人工作答。

`tests/test_question_types.py` 验证导入与判题；`scripts/shared_features_fixture.py --output <D盘新隔离目录> --real-judge` 和 `shared_features_acceptance.mjs` 验证浏览器 Lesson、Review、草稿、移动端及真实语义判题。合成内容不得发布到正式课程。
