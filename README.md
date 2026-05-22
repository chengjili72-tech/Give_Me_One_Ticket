# Give_Me_One_Ticket

一个可视化抢票原型（方案一：浏览器自动化编排型）的后端/前端最小实现。

## 功能

- 创建抢票任务（站点URL、事件名、购买按钮选择器）。
- 启动/暂停任务。
- 后端内置**自适应频率控制器**（根据延迟、阻断、验证码、成功信号动态调频）。
- 前端轮询展示任务状态（尝试次数、频率、风险分、结果）。

> 当前版本是模拟执行流，便于先验证产品架构。后续可替换 `_simulate_one_attempt` 为 Playwright/Selenium 真实动作。

## 快速启动

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --reload --port 8000
```

打开：`http://127.0.0.1:8000`

## 后续建议

- 增加站点插件化目录：`connectors/damai.py`, `connectors/12306.py`。
- 接入 Playwright 持久化上下文，支持用户手动登录后接管。
- 增加风控事件日志与告警通知（短信/邮件/企业微信）。
