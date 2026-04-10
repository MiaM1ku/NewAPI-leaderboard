# NewAPI Leaderboard

NewAPI 用量统计报告工具 —— 自动从 [NewAPI](https://github.com/Calcium-Ion/new-api) 后台拉取模型与用户用量数据，生成排行榜式的中文报告，并可通过 Webhook 推送。

## ✨ 功能

- **多周期报告**：支持 `daily`（每日）、`weekly`（每周）、`monthly`（每月）三种模式
- **模型用量 Top 5**：按消费金额排序，展示请求次数、Token 用量、消费金额
- **用户用量 Top 5**：按消费金额排序，展示请求次数、Token 用量、消费金额
- **用户模型明细**：为 Top 5 用户展示各自消费前 3 的模型，包含输入/输出/缓存 Token 细分
- **模型别名归一化**：自动从渠道的 `model_mapping` 配置中提取别名映射，合并同一模型的不同名称
- **Webhook 推送**：可选配置 Webhook URL，自动将报告推送至企业微信、飞书、Discord 等

## 📋 前置要求

- Python 3.9+
- 一个可访问的 NewAPI 实例（需管理员账号）

## 🚀 快速开始

### 1. 克隆项目

```bash
git clone https://github.com/your-username/NewAPI-leaderboard.git
cd NewAPI-leaderboard
```

### 2. 安装依赖

```bash
pip install requests python-dotenv
```

### 3. 配置环境变量

复制示例配置文件并填入实际值：

```bash
cp .env.example .env
```

编辑 `.env` 文件：

```env
BASE_URL=https://YOUR_NEWAPI_URL
NEWAPI_USERNAME=YOUR_USERNAME
NEWAPI_PASSWORD=YOUR_PASSWORD
WEBHOOK_URL=YOUR_WEBHOOK_URL
WEBHOOK_AUTHORIZATION=Bearer YOUR_WEBHOOK_AUTHORIZATION_TOKEN
```

| 变量 | 说明 | 必填 |
|------|------|------|
| `BASE_URL` | NewAPI 实例地址（不带尾部 `/`） | ✅ |
| `NEWAPI_USERNAME` | NewAPI 管理员用户名 | ✅ |
| `NEWAPI_PASSWORD` | NewAPI 管理员密码 | ✅ |
| `WEBHOOK_URL` | Webhook 推送地址 | ❌ |
| `WEBHOOK_AUTHORIZATION` | Webhook 请求的 Authorization 头 | ❌ |

### 4. 运行

```bash
# 每日报告（默认）
python main.py

# 每周报告（过去 7 天）
python main.py weekly

# 每月报告（过去 30 天）
python main.py monthly
```

## 📊 报告示例

```
📊 每日用量报告 — 2026-04-09
====================================

🔢 总请求次数: 1234
🪙 总 Token 量: 56.78 M
💰 总消费金额: $12.3456

🏆 模型 Top 5
------------------------------------
  1. claude-sonnet-4-20250514
     请求: 500  |  Token: 20.00 M  |  消费: $5.0000
  2. gpt-4o
     请求: 300  |  Token: 15.00 M  |  消费: $3.5000
  ...

👥 用户 Top 5
------------------------------------
  1. alice
     请求: 400  |  Token: 18.00 M  |  消费: $4.5000
       · claude-sonnet-4-20250514  (60.0%)  消费: $2.7000
         输入: 5.00 M  |  输出: 3.00 M  |  缓存读取: 1.00 M  |  缓存创建: 0.50 M
       · gpt-4o  (30.0%)  消费: $1.3500
         输入: 3.00 M  |  输出: 2.00 M  |  缓存读取: 0.00 M  |  缓存创建: 0.00 M
  ...

====================================
```

## ⏰ 定时执行

可配合 cron（Linux/macOS）或任务计划程序（Windows）定时运行：

```bash
# 每天早上 8:00 执行每日报告
0 8 * * * cd /path/to/NewAPI-leaderboard && python main.py daily

# 每周一早上 8:00 执行每周报告
0 8 * * 1 cd /path/to/NewAPI-leaderboard && python main.py weekly

# 每月 1 号早上 8:00 执行每月报告
0 8 1 * * cd /path/to/NewAPI-leaderboard && python main.py monthly
```

## 📁 项目结构

```
NewAPI-leaderboard/
├── main.py          # 主程序
├── .env.example     # 环境变量示例
├── .env             # 环境变量配置（不纳入版本控制）
├── .gitignore       # Git 忽略规则
└── README.md        # 项目说明
```

## 🔧 工作原理

1. 使用管理员账号登录 NewAPI，获取会话
2. 拉取渠道配置，解析 `model_mapping` 构建模型别名映射表
3. 按指定时间范围拉取模型维度和用户维度的统计数据
4. 对 Top 5 用户逐一拉取详细日志，分析每个用户的模型使用明细（含缓存 Token）
5. 生成格式化的中文报告
6. 如配置了 Webhook，自动推送报告

> **汇率**：1 USD = 500,000 Quota（NewAPI 内部额度单位）

## 📄 License

MIT
