# Awesome Paper 2.0

一个三阶段的轻量化工作流，用最简单的方式完成论文收集、AI 分类以及飞书推送。相比旧版本，Stage 2 直接调用 `llm_api` 对每篇论文做语义判断，Stage 1 改成标准 arXiv 抓取，最终推送时自动把链接切换为 papers.cool 版本。

## 核心能力
- Stage 1：直接调用 arXiv API，默认抓取上一工作日（UTC）的论文并按照分类写入 `data/raw/<日期(按论文发布日期)>/<类别>/`（周一会抓取周五的数据），也可通过 `--target-date` 或 `stage1.target_date` 指定日期。
- Stage 2：把 Stage 1 生成的分类文件逐篇发送给 LLM，合并所有结果获取三级分类 + 中文 TL;DR（终端会输出 `[Stage2]` 分类进度），并保存在 `data/daily/` 和 `data/paper_database/`。
- Stage 3：读取 Stage 2 的 json，按一级分类聚合成富文本卡片（含 Emoji 修饰和 Papers.Cool 链接）逐条推送到飞书，可设置发送间隔并在卡片之间插入提示分隔。

## 项目结构
```
awesome-paper-2/
├── awesome_paper_manager.py   # CLI 入口，负责 orchestrate 三个阶段
├── stage1_scraper.py          # Stage 1：arXiv 抓取逻辑
├── stage2_classifier.py       # Stage 2：调用 LLM 分类
├── stage3_sender.py           # Stage 3：飞书推送
├── llm_api.py                 # OpenAI 协议兼容的 LLM 封装
├── config.json                # 基本配置
├── requirements.txt           # 运行依赖
└── data/
    ├── raw/                   # Stage 1 输出
    ├── paper_database/        # Stage 2 持久化归档
    └── daily/                 # Stage 2 每日结果
```

## 快速开始
1. 创建虚拟环境并安装依赖：
   ```bash
   pip install -r requirements.txt
   ```
2. 准备 LLM 凭据（示例为 DeepSeek，任何兼容 OpenAI Chat Completions 的服务都行）：
   ```bash
   export LLM_API_KEY="your-key"
   export LLM_API_BASE="https://api.deepseek.com"   # 可选，默认为此地址
   export LLM_MODEL="deepseek-chat"                 # 可选
   ```
3. 修改 `config.json` 中的类别、飞书 webhook 以及数据目录。

## CLI 用法
- 完整流程（抓取 -> 分类 -> 推送）：
  ```bash
  python awesome_paper_manager.py full --categories cs.CL cs.CV --webhook https://xxx
  ```
  未指定参数时会使用 `config.json` 中的默认配置。该命令会自动合并多个分类的抓取结果，再统一分类与发送。

- 仅抓取 Stage 1（默认抓取当天，可用 --target-date 指定其他日期，--max-results 控制数量）：
  ```bash
  python awesome_paper_manager.py scrape --categories cs.AI --target-date 2025-10-05
  ```
  输出示例：`data/raw/20251005/csAI/raw_csAI_101500.json`

- 仅分类 Stage 2：
  ```bash
  python awesome_paper_manager.py classify --raw-file data/raw/20250101/csAI/raw_csAI_20250101_101010.json
  ```

- 仅推送 Stage 3：
  ```bash
  python awesome_paper_manager.py send --classified-file data/daily/daily_20250101.json
  ```
  发送时会按一级分类拆分成多条富文本卡片，并在每条卡片之间等待 `stage3.delay_seconds`（默认 2 秒，可在 `config.json` 中调整）。

## 数据约定
- Stage 1 输出格式（存储路径 `data/raw/<日期>/<类别组合>/raw_<类别组合>_<时分秒>.json`）：
  ```json
  {
    "generated_at": "2025-10-05T11:15:00Z",
    "categories": ["cs.CL"],
    "paper_count": 12,
    "papers": [ ... ]
  }
  ```
- Stage 2 输出会附带 LLM 返回的 `primary_area`、`secondary_focus`、`application_domain` 与 `tldr_zh`，并记录 `source_raw_files`（以及单文件时的 `source_raw_file`）以便追溯；
  同时会在 `data/paper_database/<primary_area>/<secondary_focus>/<application_domain>/` 下为每篇论文生成独立 JSON，并在 `data/daily/<日期>/daily_<日期>_<时分秒>.json` 中保留当日的完整列表。
- Stage 3 发送前会将 `arxiv_url` 转换为 `papers.cool`，无需额外处理。
- Stage 3 配置示例（可在 `config.json` 中调整批次粒度与节奏）：
  ```json
  "stage3": {
    "delay_seconds": 2,
    "separator_text": "🚧 下一类别：{label} （进度 {current}/{total}）🚧",
    "exclude_tags": ["cs.CV", "diffusion_models"]
  }
  ```
- `separator_text` 支持 `{label}`、`{current}`、`{total}` 占位符。
- `exclude_tags` 是可选的标签列表（不区分大小写）。当论文的 `primary_category`、`primary_area`、`secondary_focus`、`application_domain`（或 `tags` 字段中的任意标签）命中其中任意一项时，该论文会在 Stage 3 被跳过，不再推送。
- Stage 1 配置示例：
  ```json
  "stage1": {
    "target_date": "2025-10-05"
  }
  ```
  留空或删除 `target_date` 时会抓取上一工作日的论文（周末会回退到周五）。
- 如在周末运行且未指定 `target_date`，脚本会提示“周末无新论文”并跳过抓取。

## LLM 提示要点
Stage 2 的提示词固定在 `stage2_classifier.py` 内，会传入以下 taxonomy：
- `primary_area`: text_models | multimodal_models | audio_models | video_models | vla_models | diffusion_models
- `secondary_focus`: dialogue_systems | long_context | reasoning | model_compression | model_architecture | alignment | training_optimization | tech_reports
- `application_domain`: medical_ai | education_ai | code_generation | legal_ai | financial_ai | general_purpose

LLM 需返回 JSON：
```json
{
  "primary_area": "text_models",
  "secondary_focus": "reasoning",
  "application_domain": "general_purpose",
  "tldr_zh": "一句中文总结"
}
```

## 常见问题
- **没有抓取到论文**：确认 `target_date` 是否设定正确，或调大 `max_results`。
- **LLM 报错**：确认环境变量 `LLM_API_KEY` 是否设置，或检查服务端限流。
- **飞书推送失败**：确认 webhook 是否有权限，必要时在 CLI 中显式传入 `--webhook`。

## 自动化运行示例
- 使用 `automation_runner.py` 可实现工作日自动重试：
  ```bash
  python automation_runner.py
  ```
  默认在工作日内最多尝试 6 次，每次间隔 3600 秒；周末会直接发送“周末无论文”提醒。
- 可通过参数或 `config.json` 的 `automation` 节点调整：
  ```json
  "automation": {
    "max_attempts": 6,
    "interval_seconds": 3600
  }
  ```
  命令行参数 `--max-attempts`、`--interval` 会覆盖配置文件，`--target-date` 可覆盖日期逻辑（周末也照常执行）。
- 自动化脚本会在 `data/automation_status.json` 记录每日的阶段完成情况，避免重复执行同一天的任务。

## 下一步
可根据需要拓展：
- 在 Stage 1 对原始摘要做去噪、过滤重复。
- 在 Stage 2 增加重试机制或并行处理。
- 在 Stage 3 改成卡片消息以获得更好的排版。
