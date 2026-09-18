/** 页面级错误边界：一个页面崩了，不该让整站白屏。
 *
 * 为什么需要它：React 里未捕获的渲染异常会卸载整棵树 —— 用户看到的是**全白**，
 * 既没有信息也没有出路（现场演示时最尴尬）。有了边界，至少做到三件事：
 * 1. 明确告诉用户"是这一页出错了，其它页面还能用"；
 * 2. 给出**可照做的下一步**（重试 / 回总览），而不是让他刷新碰运气；
 * 3. 把真实错误留在界面上（可展开），而不是只进 console——
 *    这个项目的纪律是"如实呈现"，出了错也一样。
 *
 * 切换路由时用 `key` 重置边界：用户换个页面就该重新开始，不该被上一次的错误卡住。
 */

import { Component, type ErrorInfo, type ReactNode } from "react";
import { Callout } from "./ui";

export class ErrorBoundary extends Component<
  { children: ReactNode; onHome?: () => void },
  { error: Error | null }
> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // 保留到控制台便于排查；界面上也会显示（不藏）
    console.error("[page-error]", error, info.componentStack);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <div className="mx-auto w-full max-w-[860px] space-y-3">
        <Callout
          tone="warn"
          icon="⚠"
          title="这个页面出错了。"
          details={
            <pre className="mt-1 max-h-64 overflow-auto rounded-[var(--radius-sm)] bg-surface-2 p-2 text-[11px] leading-relaxed kbd-mono whitespace-pre-wrap break-all">
              {error.stack || String(error)}
            </pre>
          }
        >
          其它页面仍然可用 —— 换一个导航项即可。
        </Callout>
        <div className="flex gap-2">
          <button className="btn" onClick={() => this.setState({ error: null })}>
            重试这一页
          </button>
          {this.props.onHome && (
            <button className="btn-ghost" onClick={() => this.props.onHome?.()}>
              回总览
            </button>
          )}
        </div>
      </div>
    );
  }
}
