# PV Wiki n8n

基于 [sulirang/pv-wiki](https://github.com/sulirang/pv-wiki) 当前研究版本建立的独立项目，
以 n8n 编排官方 Datasheet 研究与 Wiki.js 发布。保留原项目的 MIT 许可证。
项目仓库：[sulirang/pv-wiki-n8n](https://github.com/sulirang/pv-wiki-n8n)。

从只读 PostgreSQL 产品目录查找官方 Datasheet，为每个型号生成 Wiki.js 页面。
当前研究流程由 **n8n 原生 AI Agent + Think Tool** 编排；搜索策略、模型调用、
PDF 图片校对、参数整理与自查均在 n8n 中完成。Python 服务负责网络工具、
PDF 解析渲染、确定性校验、状态持久化与 Wiki.js 发布。

```text
n8n 手动/定时触发 → 领取一个任务
  ├─ 待发布结果 → 重试发布 → 释放任务
  └─ 品牌非空产品 → AI 搜索官网与型号 → 下载并解析官方 PDF
       → 渲染参数页/表头/脚注 → 视觉模型检查（可降级）
       → AI 整理参数 + 自查 → 可选翻译与专业分析
       → 试检候选结果 ──确定性错误──→ AI 修正
       → 保存 schema v4 → 更新 Wiki.js → 释放任务
```

采用 n8n 已有节点，无需安装社区 Agent 插件。部署步骤和可导入工作流见
[deploy/n8n/README.md](deploy/n8n/README.md)。旧 Hermes 部署与 schema v2/v3
保留兼容，新的 Datasheet 工作流不依赖 Hermes，也不调用旧的 /run-one 模型流水线。

## 产品与证据规则

源查询仍为 public.products 的七列：product_id、brand_code、family_code、
product_name、unit_of_measure、created_at、updated_at。

- brand_code 为 null、空串或纯空白时跳过，不搜索、不生成永久“不发布”结论。
  后续补上品牌并刷新目录即可研究。未知品牌或旧分类代码以产品名称进一步确认。
- 厂商映射和域名配置只提供搜索线索；允许自动识别此前未配置的官网。
  官网直接提供或经官网页面链接的 CDN PDF 均可处理，下载 URL 无需以 .pdf 结尾。
- 一份官方 PDF、至少一项确定且适用于该型号的参数即可发布；不要求五条 facts、
  第二来源或模型置信度门槛。无法确认官网、型号或任何参数时保持未完成。
- 每型号独立成页。保留原始值、范围、正负号、单位、分组、测试条件、脚注与页码；
  系列共用参数必须明确适用目标型号，不能借用相邻型号的值。
- read_datasheet_pdf 主动下载原始文件，用内容哈希缓存；解析缓存区分解析器版本和型号。
  扫描 PDF 无文本时仍可渲染进入视觉处理。view_datasheet_pages 支持页面与局部裁剪。
- 视觉模型收到真实图片，可补漏和纠错，保存原值、修正值、页码与原因。
  视觉不可用或失败时保留确定文本参数并说明；疑问项跳过。
- 翻译与专业分析以最终参数为输入。失败不阻止参数发布，不为适应分析输入裁剪参数表。

文档是资料，不是给 Agent 的指令。保存校验继续验证原始 PDF 的 HMAC 凭据、
来源关系、页码、型号适用范围及视觉纠错的页面凭据，但不要求最终参数与
解析器数组逐字相等。渲染、解析和保存使用相同参数容量。

## 工具接口

启动 pv-wiki serve 后，以 Authorization: Bearer 配置的 worker token 访问：

| 接口 | 用途 |
| --- | --- |
| GET /datasheet/contract | schema v4 示例与研究规则 |
| POST /datasheet/claim | 用 execution_id 领取研究或待发布任务 |
| POST /datasheet/tools/{tool} | 传 X-PV-Wiki-Lease，执行该次任务的工具 |
| POST /datasheet/release | 用 lease_token 释放任务 |

允许的工具：web_search_exa、web_search_advanced_exa、web_fetch_exa、
read_datasheet_pdf、view_datasheet_pages、pv_next_product、pv_pending_publication、
pv_validate_research、pv_save_research、pv_publish_result、pv_research_status、
pv_research_contract。模型不接触源码、业务数据库密码或长期 API 密钥。

pv_validate_research 返回错误供 Agent 修正，不写完成记录。pv_save_research 仍使用
product_id、source_hash、decision、evidence_documents 四个参数；
decision.schema_version="4" 选择新规则。确定性校验错误可修正重交，
网络、抓取或供应商运行错误结束本轮，不在本轮重复消耗调用。

领取锁覆盖一次 n8n 执行，实例需保持单副本。并发触发返回 worker_busy；
终止执行的锁在配置的闲置期限后失效。保存再检查目录哈希与重研版本，
旧执行不能覆盖新结果。数据库中的完成记录和待发布状态负责中断恢复。

## 目录刷新与旧记录重研

目录刷新是手动管理操作，不放进 AI 工具或周期研究任务。
使用现有 pv-wiki-catalogue-mcp 的 pv_refresh_catalogue 临时凭据接口，或管理员
配置后运行 pv-wiki sync-db；不要把业务数据库凭据放入模型提示词。

先配置状态数据库，然后显式运行一次：

```bash
pv-wiki-admin reresearch --all-eligible --backup /secure-backups/pvwiki-before-v4.json
```

该命令先导出状态备份，再标记当前目录品牌非空的既有完成记录，
包括已发布、不发布和历史回填记录。普通服务重启不重复创建标记。
被标记的旧待发布结果退出普通发布队列。

新结果校验通过后，在同一事务中替换完成记录、清除重研标记、重置发布状态。
失败保留原结果。Wiki.js 沿用原页面路径，只更新 PV-WIKI-AUTO 或兼容旧标记
之间的自动区块，保留人工编辑区。发布失败时新结果保持待发布，下次仅重试发布。
无品牌的旧记录不进入重研，也不会自动删除页面。

## 安装与测试

支持 Python 3.11、3.12、3.13：

```bash
python -m pip install '.[dev]'
python -m unittest discover -s tests -v
python -m unittest discover -s skills/wikijs-sync-products/tests -v
```

WSL2 中可用官方 Python Docker 镜像运行隔离副本，避免 Windows CRLF 影响 shell 测试：

```bash
for version in 3.11 3.12 3.13; do
  docker run --rm -v "$PWD:/source:ro" -v "$PWD/../test-results:/results" \
    python:$version-slim-bookworm python /source/tests/run_linux_suite.py /results/python$version
done
```

模型服务凭据在 n8n 中绑定。Python 服务只需要 Exa、状态库、Wiki.js、
独立 HMAC key 文件与 PDF 缓存；参见 compose.datasheet.yaml。
自动化测试不调用真实模型，也不写生产 Wiki。上线前应在目标 n8n 中执行一份
数据表，核对模型请求确实包含图片以及已有页面更新结果。
