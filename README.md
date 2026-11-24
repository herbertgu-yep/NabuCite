# NabuCite

NabuCite 是一个面向企业知识库的知识问答工作台。管理员上传 PDF、Word 或 Markdown 后，按知识库分配阅读权限。员工可以用自然语言提问、连续追问，并点击回答中的引用，直接核对原文和 PDF 页码。

## 主要功能

- **文档入库**：支持 PDF、Word（.docx、.doc）和 UTF-8 Markdown。PDF 中没有可提取文字的页面由 Tesseract 进行简体中文和英文 OCR；Markdown 引用标记行号，Word 引用标记正文段落或表格行的位置。管理台显示处理状态，支持重试失败任务。
- **两种问答模式**：知识库问答可按需检索和补读原文；通用问答直接使用配置的生成模型。回答支持流式输出、撤回和重新生成。
- **权限与会话**：知识库可授权给个人、部门或项目组。同一知识库的员工各自拥有私人会话，打开引用时也会重新校验访问权限。
- **检索与用量**：默认使用 Elasticsearch BM25，可切换为 Milvus 向量检索、RRF 混合检索或混合检索加重排。个人和管理台均可按授权范围查看用量并导出 CSV。

## 快速开始

需要 Python 3.12、Node.js 22.13 或更高版本、pnpm 10，以及支持 Compose 和 BuildKit 的 Docker 环境。准备一个支持流式对话的 OpenAI 兼容模型服务和对应 API Key。

**1. 配置模型。** 首次运行时，在仓库根目录复制配置模板，然后编辑 `backend/.env`。已有配置可直接沿用。

```sh
cp -n backend/.env.example backend/.env
```

| 配置项 | 用途 |
| --- | --- |
| `MODEL_BASE_URL` | 模型服务的 OpenAI 兼容 API 根地址 |
| `LLM_MODEL` | 生成模型的标识 |
| `MODEL_API_KEY` | 后端访问模型服务的密钥 |

**2. 启动工作台。** 启动 Docker，在仓库根目录运行：

```sh
./start.sh
```

脚本安装依赖、启动服务、执行数据库迁移，并在空库中提示设置演示账号密码。首次构建需要下载文档解析依赖。启动完成后，打开终端打印的 **NabuCite 页面**地址；默认端口被占用时，脚本会选择可用端口。

### 首次登录与账号管理

NabuCite 采用管理员开通账号的方式。首次启动空数据库时，终端会依次提示你为 `demo_a` 和 `demo_b` 设置初始密码（各至少 15 个字符）。也可在首次启动前通过后端环境变量 `DEMO_A_PASSWORD` 和 `DEMO_B_PASSWORD` 分别指定。密码由部署者设置，后续启动沿用已有账号和密码。

**开通系统管理员**：`demo_a` 初始拥有演示知识库的管理角色。保持启动终端运行，在另一个终端进入仓库根目录，执行一次授权：

```sh
(cd backend && .venv/bin/python seed.py --system-admin demo_a)
```

**登录**：打开启动终端打印的页面地址，用户名填写 `demo_a`，密码填写首次启动时为 `demo_a` 设置的密码。登录后侧栏应出现「管理台」。已有部署可将命令中的 `demo_a` 替换为一个已启用的现有用户名，授权后仍使用该账号原密码。

**开通员工账号**：进入「管理台 → 成员与组织 → 成员 → 新增员工」，填写登录用户名、显示名、主部门和初始密码。再到「知识库管理 → 访问权限」为员工或其所属组织授权。员工使用分配的账号直接登录。

## 用自己的文档试用

1. 管理员在「管理台 → 知识库管理」新建一个库，通过「访问权限」向演示员工 `demo_b` 授予读者权限。
2. 上传一份适合该知识库成员阅读的 PDF、Word 或 Markdown 文档，等待文档状态变为「可用」。各格式的单文件上限均为 32 MiB。
3. 用 `demo_b` 登录，选择该库和「知识库问答」，围绕文档中的一条条款提问，再追问它的适用条件。
4. 点击回答中的引用编号，核对原文片段。PDF 支持打开对应页，Word 和 Markdown 可下载原件对照。

## 技术栈

| 模块 | 实现 |
| --- | --- |
| 前端 | React、TypeScript、Ant Design、Vite |
| API 与数据 | FastAPI、SQLAlchemy、Alembic、PostgreSQL |
| 文档处理 | MinIO 保存原件，RabbitMQ 和 Celery 调度任务，Docling 解析文本 PDF，Tesseract 识别扫描页 |
| 检索 | Elasticsearch BM25、Milvus HNSW、RRF 排名融合、可选模型重排 |
| 监控 | Prometheus、Grafana |

默认 BM25 模式只需配置生成模型。启用向量或混合策略前，需要配置 Embedding 服务并为已有文档补建向量；启用重排还需要 Rerank 服务。完整配置项见[后端环境模板](backend/.env.example)。

## 开发与测试

完成快速启动后，可从仓库根目录运行常规测试和前端构建：

```sh
(cd backend && .venv/bin/python -m pip install -r requirements-test.txt && .venv/bin/python -m pytest -q tests)
(cd frontend && npm test && npm run build)
```

## 分块与离线评测

新入库的文档以结构块为父块、可检索片段为子块。补读最多取同一父块内最近的三个相邻片段，可跨 PDF 页；Markdown 和 Word 保留行号或段落位置。启动时 Alembic 会添加结构字段，原有文档继续使用同页同节补读。

在 `backend/` 下运行：

```sh
.venv/bin/python evaluate_search.py dataset.json bm25 <评测用户名>
.venv/bin/python evaluate_turns.py turns.json labels.json
```

第一条在指定账号可读的单文档知识库上评测。数据集包含 `knowledge_base_id`、`document_id` 和 `cases`。每题填写 `question`、`query` 和 `evidence`，每个证据锚点填写 `document_id`、`position`（页、行或段号）和 `quote`（原文短句）。输出的 `evidence_recall_at_10` 为前十个片段中命中的必要锚点数除以全部必要锚点数。

第二条复用本地记录。每轮记录包含 `conversation_id, question, terminal, query, steps, answer, citations, readback`；`steps` 取实际进度事件，`citations` 保留编号与片段 ID，`readback` 用 `chunk_id, excerpt` 记录打开引用后的原文。标注格式示例：

```json
[{"turn":1,"previous_question":"归档多久？","question":"备份呢？","tools":["retrieve"],"query_terms":["备份","保留"],"claims":[{"text":"备份保留七天","quotes":["备份保留期为七天"]}]}]
```

`turn` 从 0 开始。按同一会话核对上一问，比较实际检索与补读顺序，检查查询是否保留人工指定的关键概念。引用支持度按人工逐条标注的回答结论及其支持原文核对，只统计已标注结论。两份输入留在本机。
