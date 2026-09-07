# n8n 官方 Datasheet 研究

当前入口是 [PV Wiki - Official Datasheet Agent](workflows/datasheet/pv-wiki-datasheet-agent.json)。
研究、模型调用、视觉校对和自我审查都在 n8n 中完成；Python 服务只提供搜索代理、
PDF 下载/缓存/解析/渲染、研究状态校验和 Wiki.js 写入。无需 Hermes。

## 运行流程

每 30 分钟或手动启动一次，原子领取一个待发布结果或一个有品牌代码的产品：

1. 有积压发布时只重试该产品的发布，然后结束；没有积压时才选新产品。
2. Research Agent 通过 Exa 搜索和抓取官方产品页，使用内置 Think 工具整理简短的
   厂商、型号、来源和资料缺口检查表，选定一份官方 PDF。
3. Python 主动下载原始 PDF，按内容哈希缓存并解析目标型号。一个系列 PDF 可以为
   多个型号分别生成页面；每次解析/保存都绑定本次目标型号。
4. n8n 顺序检查每一页。渲染出的 PNG 转为 n8n binary 数据，Visual Datasheet Check
   的 imageBinary 消息将图片真正送入视觉模型。每次包括首页、前页和当前页，
   以保留扫描版、续表及多型号表头的上下文。需要时可按 0..1 坐标裁剪放大。
5. Compose Parameters Agent 整理完整参数表；Self Review 以同一研究模型独立输出
   checklist、verdict、corrections、next_action。可返回搜索、重看图片或修正参数。
6. 可选的中文参数翻译和专业分析独立生成。失败或格式错误不会删掉参数表。
7. 先调用只读 pv_validate_research。校验通过才 pv_save_research，再 pv_publish_result。
   确定性的校验错误携带反馈回到模型修改候选；抓取、认证、会话失效等运行错误结束本轮。
8. 正常结束、已处理的错误以及修正次数耗尽都会释放会话。进程异常或手动取消后，
   服务端会话租约过期恢复；过期会话无法继续付费搜索、保存或发布。

默认最多 3 次修正、3 轮重新搜索，Agent 单次最多 30 次工具迭代。这些是可在
Configuration/Agent 节点调整的 n8n 执行界限，不是五条事实或多来源发布门槛。
只有一份官方 PDF、只有一项确定参数，也可生成产品页。品牌代码为空的产品由
服务端选择器直接跳过；补上品牌后可以进入研究。无法确认型号/官网时保留未完成。

## 部署与凭据

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

Research Chat Model 绑定用户现有的 OpenAI-compatible credential，选择实际模型 ID。
搜索、参数整理、自检、可选分析共用该节点。Vision Chat Model 优先复用同一模型；
若已有独立视觉模型则绑定其现有 credential。替换两个 REPLACE_WITH_* 占位值。
普通 OpenAI-compatible provider 保持 responsesApiEnabled=false；如需 Base URL，
使用该凭据或模型节点的受支持设置，不在 Code 节点自行请求模型。

Configuration.vision_enabled 默认 true。模型不支持图片时设为 false；
运行时图片渲染/视觉模型失败会记录 failed/unavailable 并保留明确的文本参数。
未收到成功图片校对结果时不会标记 reviewed。扫描版缺少可用文本且视觉也失败时，
本轮无法形成确定参数，会保留未完成，而不会发布空页。

服务端全局 claim 锁保证同一 worker 上手动/定时执行不会并行研究，也与旧 /run-one
互斥。只运行一个 worker 副本；默认会话 TTL 为 3600 秒，工具调用会续期。
不要另用 CLI 并发修改同一状态库。

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
- 正常 PDF、扫描版和跨页续表：查看 Visual Datasheet Check 输入的 Binary 标签，
  确认 page/previous/anchor 是 PNG；确认模型确实返回了与页面一致的识别结果。
- 不支持视觉或视觉调用失败后，确定文本参数仍可保存，并显示未视觉检查状态。
- 让一个候选参数校验失败，确认反馈送回模型且修改后才再次校验/保存。
- Self Review 的 search/view 分支确实回到搜索/图片节点；次数耗尽释放会话。
- 可选分析失败后参数数量不变；超过 200 行的参数表没有被裁掉。
- 两次并行触发只有一次获得 claim；发布失败后的下一轮只重试发布。
- 重研覆盖更新原页面，保留人工编辑区。

本地结构/数据流验收：

    python -m unittest tests.test_n8n_datasheet_workflow -v
    node deploy/n8n/workflows/datasheet/workflow-smoke-test.cjs

使用 n8n MCP 做静态验证不等于真实模型执行验收。只有在配置了可用 worker URL、
凭据和支持图片的模型后，才能完成完整在线检查并激活定时触发。
仅将有意义的失败交给现有 n8n 错误通知；未找到产品或未完成研究不是每轮通知理由。

旧 workflows/pv-wiki-product-cycle.json 与 pv-wiki-homepage-refresh.json 为回滚兼容模板。
旧产品循环调用 /run-one，并不代表当前 n8n Agent 架构；迁移后不要激活它。
