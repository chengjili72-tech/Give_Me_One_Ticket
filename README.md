# Give_Me_One_Ticket

可视化抢票工具（方案一迭代版）：用户自己登录、自己选票后，由系统执行自动点击与动态频率控制。

## 当前可执行业务流

1. 在页面输入购票网站 URL，点击“打开浏览器并跳转”。
2. 新弹出的 Chromium 窗口中，用户手动登录并进入目标票页面。
3. 回到控制台页面点击“我已登录并选好票”。
4. 填写购买按钮 CSS Selector（可选刷新按钮 Selector），创建并启动任务。
5. 后端开始真实循环：检测页面状态 -> 尝试点击购买 -> 根据风控信号自动调频。

## 技术说明

- 后端：FastAPI + Playwright（异步）
- 调频策略：成功降频、阻断/验证码指数退避、高延迟降速，并加入抖动避免固定节奏。
- 任务状态：`idle/running/paused/success/failed`

## 快速启动

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
uvicorn app:app --reload --port 8000
```

打开：`http://127.0.0.1:8000`

## 重要提醒

请仅在遵守目标网站协议与法律法规前提下使用。遇到验证码或风控页面，建议切人工处理，不应绕过平台安全机制。
