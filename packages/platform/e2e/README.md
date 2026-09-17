# e2e（浏览器主流程走查）

用真实 chromium（playwright-core，缓存浏览器）对主流程做机器断言：
1. **图谱默认骨架**：进 `/graph` 不搜索即加载 overview（56 节点 Tag 可见）；
2. **图谱 3D**：切 3D 视图画布渲染正常、适配/停转控件可用；
3. **FaultLab**：URL 直达 `?scenario=...` 自动播放且出现故障注入/高亮；
4. **Agent 症状诊断卡**：输入“仪表盘闪烁但无故障码”→ 出现症状资产与供电域候选。

## 运行

```bash
# 前置：后端已启动且 web/dist 已构建（127.0.0.1:8000）
cd e2e
npm run smoke        # 等价 node main-flow.mjs
```

- 浏览器可执行文件从本机 Playwright 缓存自动探测（无需在线下载）；
- 失败时打印首个断言错误并以非 0 退出（CI 可直接用）；
- 可用环境变量 `TCMS_E2E_BASE` 覆盖目标地址（默认 `http://127.0.0.1:8000`）。

## 说明

- 这是“真实浏览器主流程”级冒烟（Playwright 自动化、机器断言），不是截图工具；
- 老的手写走查脚本（`*-walk.mjs` / `*-verify.mjs`）保留供人工/截图使用；
- 更细粒度（播放器 seek 语义、节点点击后子图重拉）建议在 `web/src/lib/*` 层用 vitest
  锁纯逻辑，UI 层用本 smoke 守护主链路。
