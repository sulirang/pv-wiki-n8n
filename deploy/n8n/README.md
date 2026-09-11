# n8n 官方 Datasheet 研究

当前入口是 [PV Wiki - Official Datasheet Agent](workflows/datasheet/pv-wiki-datasheet-agent.json)，
生成器产物为 88 节点的逐页提取、独立自审和纠错闭环。
研究、模型调用、视觉校对和自我审查都在 n8n 中完成；Python 服务只提供搜索代理、
PDF 下载/缓存/解析/渲染、研究状态校验和 Wiki.js 写入。无需 Hermes。

## 运行流程

每 30 分钟或手动启动一次，原子领取一个待发布结果或一个有品牌代码的产品：

1. 有积压发布时只重试该产品的发布，然后结束；没有积压时才选新产品。
2. Research Agent 通过 Exa 搜索和抓取官方产品页，使用内置 Think 工具整理简短的
   厂商、型号、来源和资料缺口检查表，优先选定匹配目标型号的官方英文 PDF。
3. Python 主动下载原始 PDF，按内容哈希缓存并解析目标型号。一个系列 PDF 可以为
   多个型号分别生成页面；每次解析/保存都绑定本次目标型号。
4. `Locate Datasheet Pages` 读取带页码的完整普通文本，标识目标参数页、共用参数、
   续表及其实际表头；不明确和扫描页继续检查，相邻优化器等无关产品页不混入。
   `page_texts` 同时提供 `plain_text`、紧凑 `layout_text` 和各自的截断状态。
   Python 的确定性列解析仍使用原始 layout，空白不再挤占模型正文预算。
5. n8n 逐页提取草稿，再由独立的 `Audit Page Parameters` 或 `Audit Text Parameters`
   逐行审查数值、型号列、单位、条件、脚注和缺失行。视觉审查收到当前页的真实 PNG
   binary 消息；续表只附带前页有序表头标签，不混入前页整图或参数值。密集表格可以
   请求 0..1 坐标裁剪。修改和新增行回到审查，
   不能凭提取模型自行标注 confirmed 就进入最终表。未能确认的行省略并说明。
6. PDF 就绪、页面草稿和已审结果经流程的 `Checkpoint Progress` 保存。模型只收到
   本页或本批参数，不接收证据签名、会话令牌和整份传输状态。Research Agent 的工具
   只有三个 Exa 搜索/抓取工具与 Think；校验、保存、发布均由流程节点调用。
7. `Build Decision` 合并已确认参数，注入领取的产品、源版本和 revision。
   参数名称、文字值、条件和脚注的中文翻译按批处理，再独立审查；
   翻译失败、格式错误不会删掉参数表。当前不生成单品产品分析。
8. 先调用只读 `pv_validate_research`；确定性的参数校验错误回到对应页修正再审。
   校验通过且 `Save Enabled` 为 true 才调用 `pv_save_research`。
   Wiki.js 已配置时再发布，否则保留已保存的可发布版本。抓取、认证、模型连接等
   运行错误结束本轮，下一次从有效 checkpoint 继续，不自动重放失败的付费请求。
9. `Format Result` 后释放会话，最后 `Research Result` 输出明确的结果状态、参数和预览。
   进程异常或手动取消后等待租约过期再恢复；过期会话无法继续搜索、保存或发布。

默认每页最多 5 轮审查、最多 2 次服务端校验修正，搜索 Agent 最多 16 次工具迭代。
这些是流程执行界限，不是五条事实或多来源发布门槛；审查次数耗尽时只保留已确认行。
只有一份官方 PDF、只有一项确定参数，也可生成产品页。品牌代码为空的产品由
服务端选择器直接跳过；补上品牌后可以进入研究。无法确认型号/官网时保留未完成。

搜索/抓取工具在发出请求前发现的确定性参数错误，以 `invalid_tool_arguments` 返回
可修正反馈，Agent 可以调整参数后重交。例如 `web_fetch_exa` 的标准输入为
`{"urls":["https://manufacturer.example/datasheet.pdf"]}`，也兼容单个 `url` 并转换为数组。
这不改变真实请求失败的处理：网络、抓取、认证或模型连接错误仍结束本轮，不当作
参数格式错误反复重试。

## 来源语言与中文展示

先核对完整型号、后缀、适用地区和资料版本，再在匹配的官方资料中优先选择英文。
研究 Agent 应主动检查厂商英文产品页、下载中心和英文数据表；找不到匹配的英文版时，
允许使用其他语言的官方原件，并记录原件语言和回退原因。不能为了英文改用相近型号、
不适用地区或不匹配版本。不同语言的目录可能有不同页码、参数和脚注；更换原件后必须
重新下载、解析和审查，不能只替换已保存证据的 URL。

参数名称和文字值都提供中文展示，同时保留原文。例如，保护功能的 `Yes` / `Ja`
可显示为“支持”，`0.8 leading ... 0.8 lagging` 显示为“0.8 超前至 0.8 滞后”，
并保留各自原始值供核对。纯数值、单位、型号、标准编号、正负号和范围不能因翻译被改写。
条件与脚注先展示完整原文，再另起一段展示中文译文；原文和译文分开保存，不用中文
覆盖原始证据。翻译失败或不能确定时保留原文并说明缺少译文，确定参数仍可保存发布。
翻译仍存入兼容字段 `decision.analysis.translations`，该字段名不代表生成产品分析。
当前研究不输出分析段落或 `overall_limitations`；v4 页面仅展示产品简介、参数表、
解析/翻译缺失说明和数据表链接。产品分析留待 Wiki 积累足够产品后，单独开展横评。
翻译流程依次使用 `Prepare Translation Batches`、`Translate Parameters`、
`Merge Translations`、`Audit Translations` 和 `Read Translation Audit`；需要去掉失败
译文再提交时进入 `Retry Without Translations`。译文状态与缺失原因分别记录在
`translation_status`、`translation_omissions`，历史分析不作为本轮失败原因。

旧记录中的历史分析保留存档，v4 展示不再输出该部分。已有参数和译文可直接通过
`pv_research_result` 重渲染，无需重新搜索、下载或调用模型，也无需标记重研。
已发布页面需要在后续发布更新时应用新展示；只读重渲染不会自动修改 Wiki。

## 部署与凭据

本地已有 Docker n8n 且希望使用单 Worker + SQLite 持久状态，可按
[本地 Worker 部署](LOCAL-WORKER.md) 使用 `compose.local-worker.yaml`。

保留 n8n、产品源库、PV Wiki 状态库和 Wiki.js 数据库之间的职责分离。
先停用旧产品循环及 Hermes cron。不要同时运行旧自动化与新工作流。

- 新建专用 n8n：使用 compose.yaml；已有 Docker n8n：使用 compose.existing-n8n.yaml；
  主机/systemd n8n：使用 compose.systemd-n8n.yaml。已有实例不需要另启动 n8n/n8n-db。
- 所有方式都叠加 compose.datasheet.yaml，以持久化 PDF 缓存并挂载 Exa 密钥与证据签名密钥。
- 复制 .env.example 为 .env、worker.env.example 为 worker.env。填写产品目录只读连接、
  独立状态库和 Wiki.js 配置。设置 EXA_KEYS_PATH、EVIDENCE_KEY_PATH 指向本机密钥文件；
  挂载文件须仅让服务容器 UID 10001 读取。签名密钥应持久保留，避免已有证据无法验证。
- 模型凭据配置在 n8n Credential 界面，不能填入 workflow JSON 或 Python worker.env。
  原 AI_BASE_URL、AI_API_KEY、AI_MODEL 仅供旧回滚路径使用，新入口不读取它们。

在 deploy/n8n 下构建并启动服务（首次专用实例可再叠加 compose.bootstrap.yaml）：

    docker compose --env-file .env -f compose.yaml -f compose.datasheet.yaml config --quiet
    docker compose --env-file .env -f compose.yaml -f compose.datasheet.yaml up -d --build

已有 Docker n8n 则把 compose.yaml 换成 compose.existing-n8n.yaml。按 .env 设置其网络。
主机 n8n 则换成 compose.systemd-n8n.yaml，并在工作流 Configuration 将 worker_url
设为 http://127.0.0.1:8080。容器 n8n 使用 http://pv-wiki-worker:8080。
Python 服务保持在私有网络；无需 Execute Command 节点或 Docker socket。

导入以下文件；导入后保持 inactive：

    workflows/datasheet/pv-wiki-datasheet-agent.json

也可使用同目录的 pv-wiki-datasheet-agent.sdk.js，通过本项目配置的 n8n MCP
validate_workflow 后 create_workflow_from_code 创建未发布草稿。
build_workflow.py 可重新生成 JSON 和 SDK，修改时保持两者一致。
目录刷新独立使用受认证的 POST /refresh-catalogue 管理接口，不开放给研究 Agent。

所有 HTTP Request / HTTP Request Tool 节点绑定同一个现有 Header Auth 凭据，
名称建议为 PV Wiki Worker，Authorization 值为 Bearer 加 PV_WIKI_WORKER_TOKEN。
导出文件不包含凭据 ID。n8n MCP 对尚未绑定的旧通用凭据类型可能给出
PLAIN_GENERIC_AUTH 提示：绑定已有 Header Auth 即可；新建凭据若要求
Templated Custom Auth，可使用同一 Authorization 模板并统一修改节点认证类型。

`Research Chat Model` 负责搜索，`Page Research Model` 负责页面定位、文本提取/审查及
中文翻译及翻译审查，`Page Vision Model` 负责带图片的提取/审查。三个节点复用用户已有的
OpenAI-compatible credential 和实际模型 ID；视觉节点使用已配置的视觉路由。
导入后替换全部 `REPLACE_WITH_*` 占位值，不新增模型供应商或凭据。
普通 OpenAI-compatible provider 保持 responsesApiEnabled=false；如需 Base URL，
使用该凭据或模型节点的受支持设置，不在 Code 节点自行请求模型。

`Configuration.vision_enabled` 默认 true。关闭视觉或未取得完整图片时走文本审查，
并记录缺少视觉校对；扫描版缺少明确文本时不能凭空确认参数。模型连接失败属于运行
错误，保留此前 checkpoint 后结束本轮。`save_results=false` 可用于只审查、不保存的
验收，成功结果为 `reviewed_preview`；默认 true 才生成持久的可发布版本。

`Configuration.datasheet_hints` 是可选对象，默认 `{}`；键为目录中的 `product_id`，
值为该产品的官方 PDF 候选 URL 数组，例如：

```json
{
  "datasheet_hints": {
    "example-product-id": ["https://manufacturer.example/datasheet.pdf"]
  }
}
```

这里可复用用户提供或此前已核实的资料地址。流程只把当前领取产品对应的 URL
交给 n8n Agent 作为查证线索，仍须抓取并确认完整型号、地区、版本和语言，随后
下载原始 PDF、独立解析与审查。候选 URL 不等于已验证证据，不绕过原有官方来源
校验，也不建立新的硬编码厂商白名单。

服务端全局 claim 锁保证同一 worker 上手动/定时执行不会并行研究，也与旧 /run-one
互斥。只运行一个 worker 副本；默认会话 TTL 为 3600 秒，工具调用会续期。
不要另用 CLI 并发修改同一状态库。

## 进度恢复与查看产物

`Checkpoint Progress` 是鉴权 HTTP 节点，写入 `product_research_checkpoints`，与完成记录
分开；保存 PDF、页面计划、草稿和简短审查/修正记录，不保存密钥、租约或图片 base64。
下一次领取优先恢复仍匹配目录 `source_hash` 和当前 revision 的进度，取得新租约后
继续 n8n 研究。旧源版本、旧工作流契约不当作新的完成结果；最终结果仍须校验再保存。
保存完成时清除对应 checkpoint。保留状态卷、PDF 缓存和签名密钥，重启不用重新搜索
或重新处理已经保存进度的页面；尚未写入 checkpoint 的最后一步可能需要重新执行。

在 n8n **Executions → 对应执行 → Research Result → Output / JSON** 查看产物。
不要只依据执行的绿色 success 标记判断产品已发布；可处理的未完成结果也正常结束。

| `outcome` | 含义 |
| --- | --- |
| `published` | 已保存并发布到 Wiki，查看 `wiki_path` |
| `publishable` | 已保存可发布版本，查看 `markdown`、`parameters`；Wiki 未配置或发布待重试 |
| `reviewed_preview` | 校验通过但关闭了保存，只提供本轮参数预览 |
| `incomplete` | 未形成新的完成记录，查看 `error`、`omissions`、`review_summary` |
| `no_work` | 没有领取到任务，结果在 `No Work Result` 节点 |

`saved`、`published` 和 `session_released` 分别报告三个动作。已有结果也可通过复用
Worker Header Auth 的 HTTP 节点调用 `POST /datasheet/tools/pv_research_result`，请求体为
`{"product_id":"目标产品ID"}`。该只读接口不需要研究租约，返回保存的参数、Markdown、
Wiki 路径或未完成进度摘要；Worker 仍仅在 Docker 私有网络可达。

## 目录、覆盖与上线验收

日常研究不自动刷新业务数据库。手动执行 pv-wiki sync-db，或从独立的 n8n
手动 HTTP Request 节点调用 POST /refresh-catalogue（复用 Worker 凭据）。该接口
同时刷新研究目录快照，来源查询仍为 public.products 的七列；它不开放给研究 Agent。

旧完成记录按需重研：

    pv-wiki-admin reresearch --all-eligible

该命令先备份状态库，再标记当前目录中品牌代码非空的完成记录。
普通重启不重复标记。新结果验证成功才替换旧记录；失败保留旧内容；
发布失败只留下待发布状态。已有 Wiki 路径及人工编辑区继续保留。
不要在已经激活的研究执行中并发运行此管理命令。

首次手动验收使用私有未发布的 Wiki.js 页面，至少检查：

- 单份官网 PDF、1～4 项参数，及一个 PDF 覆盖多个型号的情况。
- 正常 PDF、扫描版和跨页续表：查看 `Audit Page Parameters` 输入的 Binary 标签，
  确认当前页是实际 PNG，并核对审查回答与原表一致；提取成功不代替独立审查。
- 不支持视觉或视觉调用失败后，确定文本参数仍可保存，并显示未视觉检查状态。
- 让一个候选参数校验失败，确认反馈送回模型且修改后才再次校验/保存。
- 错值被修正、漏项被补回后再次审查；最终结果中能看到保留的参数和省略原因。
- 在已写 checkpoint 后中断，下一轮恢复该产品，不重新付费搜索、不重复建页。
- 翻译失败后参数数量不变；超过 200 行的参数表没有被裁掉。
- 新 v4 结果不生成产品分析；旧 v4 结果重渲染后隐藏历史分析，并保留参数和双语条件/脚注。
- 两次并行触发只有一次获得 claim；发布失败后的下一轮只重试发布。
- 重研覆盖更新原页面，保留人工编辑区。

本地结构/数据流验收：

    python -m unittest tests.test_n8n_datasheet_workflow -v
    node deploy/n8n/workflows/datasheet/closed-loop-review-smoke-test.cjs
    node deploy/n8n/workflows/datasheet/closed-loop-workflow-smoke-test.cjs

在安装了 n8n 的同版本无网络测试容器中，另运行 `from-ai-smoke-test.cjs`，验证真实
legacy/VM 表达式、工具参数、保存 payload 和模型上下文隔离。`workflow-smoke-test.cjs`
现为兼容入口，转调当前 `closed-loop-workflow-smoke-test.cjs`。

使用 n8n MCP 做静态验证不等于真实模型执行验收。只有在配置了可用 worker URL、
凭据和支持图片的模型后，才能完成完整在线检查并激活定时触发。
仅将有意义的失败交给现有 n8n 错误通知；未找到产品或未完成研究不是每轮通知理由。

旧 workflows/pv-wiki-product-cycle.json 与 pv-wiki-homepage-refresh.json 为回滚兼容模板。
旧产品循环调用 /run-one，并不代表当前 n8n Agent 架构；迁移后不要激活它。
