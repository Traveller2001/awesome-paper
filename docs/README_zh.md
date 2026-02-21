# Awesome Paper 3.0

**[English](../README.md) | 中文**

---

Agentic 论文追踪工具：通过自然语言对话配置 arXiv 论文抓取、LLM 智能分类、多渠道推送。

## 核心特性

- **单入口对话式 Agent**：`python run.py` 启动，通过自然语言完成所有配置和操作
- **智能引导**：启动时自动检测配置状态，缺少什么自动引导补全
- **异步并行分类**：基于 `asyncio` + `AsyncOpenAI` 并行调用 LLM，可配置并发数
- **插件化推送**：Notifier 插件架构，当前支持飞书，易于扩展
- **多 LLM 后端**：支持任何 OpenAI 兼容 API（DeepSeek、OpenRouter、OpenAI 等）
- **Profile 配置系统**：所有配置持久化在 `profiles/` 下，对话中修改即时生效
- **断点续传**：pipeline 状态持久化，重启后自动跳过已完成阶段
- **Supervisor 精简上下文**：自动捕获 pipeline 噪音输出，Agent 只接收精简摘要

## 快速开始

```bash
pip install -r requirements.txt
# 编辑 profiles/default.json 填入 LLM API 信息
python run.py
```

Agent 启动后会自动检测配置状态并引导补全。示例对话：

```
Assistant: 当前配置：arXiv 分类 ✓ / 兴趣标签 ✗ / 推送渠道 ✗ / LLM ✓
  你想先配置兴趣标签，还是直接运行 pipeline？

You: 我关注 reasoning、multi-agent 和 RAG 方向
Assistant: 已添加 3 个兴趣标签。要运行 pipeline 吗？

You: 好的，跑一下
Assistant: 完成！抓取 265 篇论文，分类结果保存到 data/daily/。
```

## Pipeline Supervisor

`PipelineSupervisor` 在 Agent 与 Orchestrator 之间充当监控层：

- **捕获噪音**：重定向 `stdout`，屏蔽 orchestrator/classifier 内部的 `print()` 输出
- **精简报告**：将结果压缩为一句话摘要 + 结构化字段，避免撑爆 Agent 上下文
- **工具结果压缩**：`query_papers` 截断到 10 条且只保留 title/area；`show_config` 剥离 keywords/schedule/data_dirs

## 配置字段

| 字段 | 说明 |
|------|------|
| `subscriptions.categories` | arXiv 分类列表 |
| `subscriptions.interest_tags` | 兴趣标签，用于优先排序和过滤 |
| `channels` | 推送渠道列表（可选） |
| `llm.analyzer` | 分类器 LLM 配置 |
| `llm.agent` | 对话 Agent LLM 配置 |
| `llm.*.max_concurrency` | 并行调用 LLM 的最大并发数 |

> 只需手动配置 `llm` 部分的 API 信息，其余都可通过对话让 Agent 自动配置。

## 扩展开发

**添加推送渠道**：在 `notifiers/` 下创建文件，继承 `BaseNotifier`，实现 `send_digest()` / `from_channel_config()`，在 `NOTIFIER_REGISTRY` 注册。

**添加论文源**：在 `sources/` 下创建文件，继承 `BaseSource`，实现 `fetch()` / `save_raw()`。

## 常见问题

**周末没论文？** arXiv 工作日更新，可指定日期：`帮我跑一下 2月12号的论文`。

**切换 LLM？** 对话中说 `把分类器换成 GPT-4o-mini`，或直接编辑 `profiles/default.json`。

**提高分类速度？** 增大 `llm.analyzer.max_concurrency`（默认 5），取决于 API 速率限制。

**不配推送渠道？** 可以。结果保存在 `data/daily/`，对话查询：`搜一下 reasoning 相关的论文`。
