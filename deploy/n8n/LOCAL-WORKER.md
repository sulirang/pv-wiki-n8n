# 本地已有 n8n 的 Worker

`compose.local-worker.yaml` 在现有 Docker n8n 旁运行单个 Python Worker 和专用 DNS 转发服务。
n8n 负责搜索决策、逐页提取、自审纠错和所有模型调用；Worker 提供既有搜索代理、
PDF 下载/解析/渲染、校验、持久化和 Wiki 写入，不调用研究模型。
这个本地方案将 SQLite 状态库和 PDF 缓存一起持久化到 `worker_data` 卷。
产品源 PostgreSQL 和 Wiki.js 仍通过各自的连接配置访问。

## 首次配置

在 WSL2 的 `/etc/pv-wiki` 中准备以下私有文件，不放入 Git 仓库：

- `exa-keys`：原始 Exa API key，支持多个 key。
- `evidence-hmac-key`：至少 32 字节的独立随机签名密钥，部署后应保留。
- `worker.env`：`PV_WIKI_WORKER_TOKEN`（至少 32 字符随机值），以及稍后补全的
  产品源库和 Wiki.js 设置，字段参见 `worker.env.example`。

前两个文件须为 `0600`，所有者为容器 UID/GID `10001:10001`。
`worker.env` 仅供 Compose 读取，应为 root 所有、`0600`。
本地 SQLite 模式不设置 `PV_WIKI_STATE_DATABASE_URL`。
如需覆盖文件位置，在忽略的 `.env` 中配置 `EXA_KEYS_PATH`、
`EVIDENCE_KEY_PATH` 和 `PV_WIKI_WORKER_ENV_PATH`。

从 `deploy/n8n` 目录以有权访问这些文件的用户运行：

```sh
docker network create pv-wiki-local
docker network connect pv-wiki-local n8n
docker compose -f compose.local-worker.yaml config --quiet
docker compose -f compose.local-worker.yaml up -d --build
docker compose -f compose.local-worker.yaml ps
```

网络已存在或 n8n 已连接时跳过相应命令。若 n8n 容器被重新创建，须重新接入此网络；
也可在 n8n 自己的 Compose 中声明这个 external 网络。
`PV_WIKI_DNS_IPV4` 须设为该网络中的空闲固定地址；本机网络是 `172.18.0.0/16`，
使用 `172.18.0.53`。在不同网段部署前应调整 `.env` 中的这个值。
Worker 不映射主机端口，n8n 内部地址为 `http://pv-wiki-worker:8080`。
不要使用 `docker compose down -v`，它会删除本地研究状态和缓存。

## n8n 连接

创建 Header Auth 凭据 `PV Wiki Worker`，header name 为 `Authorization`，
value 为 `Bearer ` 加同一个 Worker token。绑定工作流中的所有 Worker HTTP 节点。
凭据由 n8n 加密保存；导出的工作流只包含凭据引用。

当前生成器输出 88 节点工作流。已有模型凭据可以复用；`Research Chat Model`、
`Page Research Model`、`Page Vision Model` 都须选择真实模型，不能保留
`REPLACE_WITH_*`。视觉节点继续使用已配置的视觉模型及凭据。
导入更新前导出备份工作流，保留原凭据和模型选择；更新后刷新 n8n 编辑页，
避免用旧页面中的草稿覆盖新流程。先保持 inactive，完成实际验收再启用定时执行。

## 验证与开始研究

`GET /healthz` 检查状态服务。带 Worker 凭据的 `GET /datasheet/contract`
可确认 schema v4 工具接口可达。首次手动执行时，如果还没有导入目录，
`Claim One Item` 返回 `catalogue_not_loaded` 并正常结束。
这是连通验证，尚未执行产品搜索、模型提取/翻译或 Wiki 发布。

补全 `worker.env` 中的只读源库连接及 Wiki.js 设置后，重新 `up -d` 加载环境。
使用 `pv-wiki sync-db` 或鉴权 `POST /refresh-catalogue` 导入产品目录。
若源库 TLS 需要自定义 CA，另挂载 CA 文件并设置 `PGSSLROOTCERT` 为容器内路径。
完成一次实际产品的手动验收后，再发布工作流以启用定时研究。

更新代码后再次执行 `up -d --build`；持久卷、签名密钥和 n8n 凭据保持可复用。

## 自审、恢复和结果位置

`Read Original PDF` 返回每页 `plain_text`、紧凑 `layout_text` 和截断状态。
`Locate Datasheet Pages` 确定目标页及真实表头；提取和独立的 `Audit Page Parameters`
只接收当前页 PNG、该页正文与候选参数，逐行核对、补漏和纠错。跨页续表可以附带前页
型号列的有序标签及来源作为参考，不附带前页整图、全文或参数值；表头不能指向后续页。
修正后的行再次审查，不确定项会省略并说明。完整参数表由流程合并；翻译失败
仍保留参数，并在通过只读校验、开启保存后保存可发布版本。

搜索优先采用厂商官方英文数据表，但完整型号、后缀、适用地区和资料版本必须先匹配。
没有找到匹配英文版时可用其他语言的官方原件，记录原件语言和回退原因；更换语言版本
仍须重新解析、核对页码和脚注，不能把旧结果的证据链接直接换成新文件。
Wiki 展示给参数名称和文字值提供中文，并保留原文；条件和脚注展示原文后另起一段
显示中文译文。翻译不改动数值、单位、型号或标准编号；翻译失败时保留原文并说明，
不阻止已确认参数保存。中文翻译及翻译的独立审查仍由 n8n 模型调用完成。

当前已取消单品产品分析阶段，待 Wiki 积累足够产品后再单独开展横评。
`decision.analysis.translations` 仅保留为译文的兼容存储字段，不生成分析段落或
`overall_limitations`。v4 页面不展示产品分析；旧记录中的历史分析仍留档。
已有参数和译文可直接经 `pv_research_result` 重渲染，不需要重新搜索、模型调用或重研
标记。该只读操作不会自动更新已发布页面；页面通过后续发布更新应用新展示。

`Checkpoint Progress` 在 `worker_data` 的状态库中保存 PDF-ready、页面草稿和已审进度。
它不是完成记录，不能直接发布；不保存模型密钥、租约或图片 base64。下次领取会检查
目录源哈希和 revision，恢复匹配的进度并取得新租约；完成保存后清除 checkpoint。
手动取消执行时等待旧租约结束，保留状态卷和缓存，即可从最近保存的页面进度继续。
保存在 checkpoint 前中断的最后一步可能重做；恢复后仍执行最终校验。

打开 n8n **Executions → 对应执行 → Research Result → Output / JSON**：

- `publishable`：已保存，`markdown` 是 Wiki 内容预览，`parameters` 为最终参数表。
- `published`：已发布，另有 `wiki_path`。
- `reviewed_preview`：校验通过但 `Configuration.save_results=false`，未持久保存。
- `incomplete`：查看 `error`、`omissions`、`review_summary`，不要误认为已产出页面。

最后节点在 `Release Research Session` 之后，`session_released` 表示释放结果。
没有领取到任务时看 `No Work Result`。绿色执行 success 本身不等于保存或发布成功。
已有结果也能通过鉴权 `POST /datasheet/tools/pv_research_result` 查询，JSON 请求体
为 `{"product_id":"目标产品ID"}`；该只读操作不用研究租约，仍须 Worker Header Auth。
没有 Wiki.js 配置时可先检查保存的可发布版本，配置完成后再重试发布。

以下为 2026-09-08 双语版本的历史验收，不代表 2026-09-11 取消单品分析后的部署验收。
当时的 88 节点版本完成了真实模型纠错、图片审查、checkpoint 恢复和可发布稿保存验收。
执行 12 保存了旧荷兰文原件的 45 项参数，执行 13、14 读回结果而未重复研究。
之后执行 15～17 暴露的工具参数、型号绑定和页面归属问题已修正；失败时保留旧完成
记录。执行 18 于 2026-09-08 16:34:55（北京时间）成功保存官方英文原件的 47 项参数，
并完成中文展示验收：文字值提供中文并保留原文，两条条件及携带脚注 1/6 的四行参数
均在原文后另段展示中文。翻译和保存前后的原始参数数组一致。

当时状态库只有一条完成记录，旧结果已替换为 47 项英文来源参数；重研状态为 `replaced`，
待重研标记和 checkpoint 均已清除。当时鉴权只读接口重取的 Markdown 与执行 18 完全一致。
执行 18 的历史输出与 Worker 读回记录保留在仓库外 `../artifacts/bilingual-live/`；
该目录的当前 Markdown 可随展示规则更新，不能当作历史执行的原始输出。完整测试和历次执行见
[验收记录](workflows/datasheet/VALIDATION.md)。Wiki.js 连接仍未配置，因此结果为
`publishable`、`saved:true`、`published:false`，尚未上线；下方首次部署记录为历史状态。

## 取消单品分析的部署记录（2026-09-11）

当前 n8n 工作流已更新为仅做参数翻译及独立翻译审查，六个分析相关节点已更名。
仍为 88 个节点，保持 inactive；最终读回的节点参数与正确解码的 UTF-8 模板一致，
原有模型选择、凭据、Configuration 和拓扑保持不变。123,635 字符的生成 SDK 验证
无警告。部署前的工作流备份为
`/etc/pv-wiki/workflow-backups/before-translation-only-20260911T065412Z.json`。
最终 Worker 镜像已重建并成功重启，`/healthz` 返回 HTTP 200；已通过鉴权只读接口
导出该产品并核对部署后的展示结果。

本轮通过 86 项后端目标测试（`test_datasheet_decision.py`、`test_n8n_api.py`、
`*render*.py`），另通过 11 项 Python 工作流测试、48 项 review core 检查、350 行
翻译及失败降级 smoke、页面上下文 smoke，以及真实 n8n 2.37.10 的 legacy/VM 隔离检查。
本轮没有重新运行 Python 3.11/3.12/3.13 完整矩阵，也没有开展新的端到端模型研究。

现有记录的 47 项原始参数及中文译文完整保留，历史分析仍在数据库中，v4 页面不再展示。
若历史简介含 U+FFFD 替换字符，展示时只使用已知厂商和型号生成中性简介，不修改数据库。
当前文稿为仓库外 `../artifacts/translation-only/HUAWEI-SUN2000L-3.68KTL.md`；
`bilingual-live` 目录的当前 Markdown 已同步新展示，旧 `execution-18-publishable.json`
作为历史记录保持原样。只读导出的参数数组与执行 18 的 `result.parameters` 完全一致，
参数表逐字一致，中文译文、条件和脚注完整；页面没有分析章节、误报分析失败或 U+FFFD。
简介仅使用已知的 `Huawei SUN2000L-3.68KTL` 恢复显示。数据库 decision 的 SHA-256
仍为 `a01eb1d774fe93d6b6a9cecf86a5da905e09cd956ece72af3b7e706c4aeba74e`，
`completed_at` 未变；`../artifacts/translation-only/render-checks.json` 全部检查通过。
最终验收记录见同目录 `acceptance.json`；两个目录的当前 Markdown 均与 Worker
读回逐字一致，仅忽略文件末尾换行。
本轮未触发模型、Exa 或 PDF 研究，也没有发布 Wiki。

## 本机部署验收（2026-09-07）

- WSL2 Debian 中的现有容器 `n8n` 已接入 `pv-wiki-local` 网络。
- Worker 容器为 `pv-wiki-local-pv-wiki-worker-1`，镜像 `pv-wiki-worker:local`，
  持久卷为 `pv-wiki-local_worker_data`，以 UID 10001 运行，健康状态为 healthy。
- 运行配置位于 WSL 的 `/etc/pv-wiki/worker.env`；Exa 和证据签名密钥分别在
  `/etc/pv-wiki/exa-keys`、`/etc/pv-wiki/evidence-hmac-key`。
- [当前工作流](http://localhost:5678/workflow/5JP6B0OgKH59sGmW) 的 13 个 Worker
  HTTP 节点已绑定加密凭据 `PV Wiki Worker`。
- `/healthz` 和鉴权 `/datasheet/contract` 返回 200；Exa 配置与签名密钥成功加载，
  PDFium/Pillow 成功渲染 PNG。n8n 手动执行编号 `1` 成功，领取接口返回
  `catalogue_not_loaded`。此次验收未调用研究模型，也未发布 Wiki 页面。
- 待补齐只读源库连接、Wiki.js 连接和 Research Chat Model 的真实模型 ID。
  用户已选择的视觉模型和现有模型凭据保持原配置，工作流尚未启用定时执行。
- 修改前的 n8n 数据库备份位于 WSL 的
  `/etc/pv-wiki/n8n-backups/before-worker-20260907T062644Z.sqlite3`。

## 产品目录接入（2026-09-08）

用户提供的源数据库连接已保存到 WSL `/etc/pv-wiki/worker.env`，权限为 `0600`。
服务端的 PostgreSQL SSL 协商返回不支持 TLS，因此该连接显式使用 `PGSSLMODE=disable`。
Worker 已重新加载配置，并通过鉴权 `/refresh-catalogue` 在只读事务中读取
`GTI.public.products` 的既定七列。

本次活动目录共 1,531 条，其中 805 条品牌代码非空、726 条空品牌被研究选择器跳过，
共有 28 个非空品牌代码。目录 generation 为 1，刷新时间为 2026-09-08 09:05（北京时间）。
导入前的状态备份在 `/etc/pv-wiki/backups/state-before-catalogue-20260908T010437Z.sqlite3`。
此次仅刷新目录，没有启动搜索、模型或 Wiki 发布。

## 数据表 DNS 修复（2026-09-08）

执行 5、6 已找到官方资料，但宿主网络把华为域名解析成 `198.18.*` 和私有 IPv6，
因此 Worker 的公网地址校验拒绝下载。直接查询公共 DNS 也被改写。
本地 Compose 现在增加 `pv-wiki-dns`，仅供 Worker 使用；它通过经证书验证的
DNS-over-TLS 查询 Cloudflare。CoreDNS 镜像固定内容摘要，配置为 `Corefile.worker`。
DNS 端口只在 Docker 网络内可用，n8n 的解析设置保持原样。
实现依据：[CoreDNS forward 官方说明](https://coredns.io/plugins/forward/)。

本次没有修改下载器的公网 IP 校验、连接地址固定、HTTPS 证书校验或重定向检查。
修复后 Worker 将 `solar.huawei.com` 解析为真实公网地址，并成功下载执行 6 的
SUN2000-8KTL-M1 高电流版系列数据表：2 页、52 条候选参数，2 页 PNG 均成功渲染。
原 PDF 与解析缓存存入持久卷；此验证未调用模型，也未写研究完成记录或发布 Wiki。
