import { useEffect, useState } from "react";
import { NavLink, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { Dashboard } from "./pages/Dashboard";
import { GraphWorkspace } from "./pages/GraphWorkspace";
import { AssetsPage } from "./pages/AssetsPage";
import { ScenariosPage } from "./pages/ScenariosPage";
import { AgentPage } from "./pages/AgentPage";
import { FaultLabPage } from "./pages/FaultLabPage";
import { SettingsPage } from "./pages/SettingsPage";
import { OnboardingModal } from "./components/OnboardingModal";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { api, type SettingsView } from "./api";
import { StatusDot } from "./components/ui";
import { applyTheme, currentTheme, toggleTheme } from "./lib/theme";

const NAV = [
  { to: "/", label: "总览", icon: "◫", hint: "系统状态与入口" },
  { to: "/assets", label: "测试资产", icon: "▤", hint: "报文 · 信号 · 故障 · 需求" },
  { to: "/scenarios", label: "场景执行", icon: "▶", hint: "跑真实故障场景" },
  { to: "/faultlab", label: "故障演示", icon: "⚙", hint: "故障发生过程动画演示" },
  { to: "/graph", label: "知识图谱", icon: "◈", hint: "检索领域知识" },
  { to: "/agent", label: "AI Agent", icon: "✦", hint: "指挥测试 Agent" },
  { to: "/settings", label: "设置 / 引导", icon: "⚙", hint: "资产源 · API · 扩展点 · 新手引导" },
];

const TITLES: Record<string, { t: string; s: string }> = {
  "/": { t: "总览", s: "系统状态与从这里开始" },
  "/assets": { t: "测试资产", s: "列车视角的真实资产：DBC 报文 / FMEA 故障 / RTM 需求" },
  "/scenarios": { t: "场景执行", s: "在真实 TCMS 引擎上运行故障场景，看断言证据" },
  "/faultlab": { t: "故障演示", s: "把真实故障场景变成可播放的动画：注入 → 检测 → 处置 → 恢复" },
  "/graph": { t: "知识图谱", s: "用自然语言检索 TCMS 领域知识，结果附证据链" },
  "/agent": { t: "AI Agent 工作台", s: "给 Agent 一个真实测试任务，看它自主完成" },
  "/settings": { t: "设置 / 新手引导", s: "资产源 · AI/API · 引擎 · 外接配置" },
};

type SysStatus = {
  engine: { ok: boolean; version?: string; reason?: string };
  llm_key: boolean;
  asset_mode: string;
  /** 界面构建标识（后端从 index.html 的产物哈希与时间派生）：回答"我开的是哪一版" */
  web_build?: { available: boolean; hash: string; built: string };
  capabilities: Record<string, boolean>;
  fix_hints: { engine: string[]; llm: string[] };
};

export default function App() {
  // 窄屏（小笔记本/分屏）默认收起侧栏：196px 的常驻侧栏在 900px 宽下要吃掉 22% 的
  // 横向空间，而侧栏内容是"偶尔点一下"的导航——默认收起、需要时再展开更合理。
  // 只在初始化时判断，不与用户的手动切换打架。
  const [collapsed, setCollapsed] = useState(
    () => typeof window !== "undefined" && window.innerWidth < 1180,
  );
  const [sys, setSys] = useState<SysStatus | null>(null);
  const [settings, setSettings] = useState<SettingsView | null>(null);
  const [bannerDismissed, setBannerDismissed] = useState(false);
  const [theme, setTheme] = useState<"dark" | "light">(currentTheme());
  const loc = useLocation();
  const navigate = useNavigate();
  const meta = TITLES[loc.pathname] ?? TITLES["/"];

  useEffect(() => {
    api.systemStatus().then(setSys).catch(() => undefined);
    api.settingsGet().then((s) => {
      setSettings(s);
      // 服务端持久化的主题偏好 → 覆盖本地（多端一致）
      if (s.theme && s.theme !== currentTheme()) {
        applyTheme(s.theme as "dark" | "light");
        setTheme(s.theme as "dark" | "light");
      }
    }).catch(() => undefined);
  }, []);

  const engineOk = sys?.engine.ok ?? true; // 未知时不打扰
  const needsEngine = !engineOk;
  const showBanner = needsEngine && !bannerDismissed;
  const onboardingPending = settings ? !settings.onboarding_done : false;
  const [onboardingOpen, setOnboardingOpen] = useState(true);
  // 引导完成回调：刷新状态 → 弹窗消失（settings.onboarding_done 已 true）
  const handleOnboardingDone = (s: SettingsView) => {
    setSettings(s);
    setOnboardingOpen(false);
  };

  return (
    <div className="flex h-full">
      {/* 首次使用引导弹窗（未完成时展示；完成或"跳过"关闭——跳过保留未完成，下次启动再提醒） */}
      {onboardingPending && onboardingOpen && settings && (
        <OnboardingModal settings={settings} onComplete={handleOnboardingDone} onDismiss={() => setOnboardingOpen(false)} />
      )}
      {/* 侧栏 */}
      <aside
        className={`shrink-0 flex flex-col bg-surface border-r border-line transition-all ${
          collapsed ? "w-[60px]" : "w-[196px]"
        }`}
      >
        <button
          onClick={() => setCollapsed((c) => !c)}
          className="flex items-center gap-2 px-3.5 h-14 border-b border-line hover:bg-surface-2 transition-colors shrink-0"
          title={collapsed ? "展开导航" : "收起导航"}
          aria-label={collapsed ? "展开导航" : "收起导航"}
        >
          <span className="text-[15px] font-semibold text-ink tracking-[-0.02em] whitespace-nowrap">
            TCMS<span className="text-info">×AI</span>
          </span>
          {!collapsed && (
            <span className="text-[10.5px] text-ink-faint whitespace-nowrap">列车测试平台</span>
          )}
          <span className="ml-auto text-ink-faint text-xs shrink-0">{collapsed ? "»" : "«"}</span>
        </button>
        <nav className="flex-1 py-2 space-y-0.5 overflow-y-auto">
          {NAV.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.to === "/"}
              title={collapsed ? `${n.label} — ${n.hint}` : n.hint}
              className={({ isActive }) =>
                `flex items-center gap-2.5 mx-1.5 px-2.5 py-[7px] rounded-[var(--radius-md)] text-[13px] transition-colors whitespace-nowrap ${
                  isActive
                    ? "bg-info/10 text-info font-medium"
                    : "text-ink-dim hover:text-ink hover:bg-surface-2"
                }`
              }
            >
              <span className="w-4 text-center text-sm shrink-0">{n.icon}</span>
              {!collapsed && <span>{n.label}</span>}
            </NavLink>
          ))}
        </nav>
        {/* 底部状态：引擎真实状态 */}
        <div className="p-3 border-t border-line-soft shrink-0" title={sys ? `引擎 ${engineOk ? "可用" : "缺失"} · 资产源 ${sys.asset_mode}` : undefined}>
          {collapsed ? (
            <StatusDot tone={engineOk ? "ok" : needsEngine ? "warn" : "ok"} pulse={!engineOk} />
          ) : (
            <div className="space-y-1">
              <div className="flex items-center gap-2 text-[11px] text-ink-dim">
                <StatusDot tone={engineOk ? "ok" : needsEngine ? "warn" : "ok"} pulse={!engineOk} />
                <span>
                  {engineOk
                    ? sys?.engine.version
                      ? `TCMS 引擎 v${sys.engine.version}`
                      : "TCMS 引擎就绪"
                    : "TCMS 引擎未启用"}
                </span>
              </div>
              {sys && !engineOk && (
                <button
                  className="text-[10px] text-warn hover:underline"
                  onClick={() => setBannerDismissed(false)}
                >
                  ⚠ 如何启用引擎 →
                </button>
              )}
              {/* 界面构建标识：这台机器上可能同时有多份副本（monorepo / 旧仓 / 发布包），
                  界面长得一样、端口也可能一样。显示构建时间与产物哈希，
                  用户一眼就能确认自己打开的是哪一版，不必去翻目录或问人。 */}
              {sys?.web_build?.available && (
                <div
                  className="text-[10px] text-ink-faint/80 truncate"
                  title={`界面构建：${sys.web_build.built}（产物 ${sys.web_build.hash}）\n如果时间不是你预期的那一版，说明启动的是另一份副本（例如旧仓库或旧的发布包），不是这个仓库。`}
                >
                  界面 {sys.web_build.built.slice(5)} · {sys.web_build.hash}
                </div>
              )}
            </div>
          )}
        </div>
      </aside>

      {/* 主区 */}
      <main className="flex-1 min-w-0 flex flex-col">
        {/* 全局引导横幅（引擎缺失时，可关闭） */}
        {showBanner && sys && (
          <div className="border-b border-warn/30 bg-warn/10 px-6 py-2.5 flex items-start gap-3 shrink-0">
            <div className="flex-1 text-[12px] leading-5">
              <span className="font-semibold text-warn">场景执行与 Agent 需要 TCMS 引擎</span>
              <span className="text-ink-dim">
                {" "}
                — 当前只启用了资产浏览与知识图谱。启用方法（任选其一）：
              </span>
              <div className="mt-1 text-ink font-mono text-[11px] space-y-0.5">
                {sys.fix_hints.engine.map((h, i) => (
                  <div key={i}>· {h}</div>
                ))}
              </div>
            </div>
            <button className="btn-ghost btn-sm shrink-0" onClick={() => setBannerDismissed(true)}>
              知道了
            </button>
          </div>
        )}

        {/* 新手引导提示（首次使用，可进设置完成；弹窗打开时隐藏避免双入口） */}
        {onboardingPending && !showBanner && !onboardingOpen && (
          <div className="border-b border-info/25 bg-info/8 px-6 py-2 shrink-0 flex items-center gap-3">
            <span className="text-[12px] text-info">第一次用？花 1 分钟走一遍引导（可选：接入自己的 AI/API 与资产）。</span>
            <button className="btn btn-sm shrink-0" onClick={() => setOnboardingOpen(true)}>
              打开引导 →
            </button>
            <NavLink to="/settings" className="btn-ghost btn-sm shrink-0">
              去设置
            </NavLink>
          </div>
        )}

        {/* topbar */}
        <header className="flex items-baseline gap-3 px-5 lg:px-7 pt-5 pb-1 shrink-0">
          <h1 className="text-[18px] font-semibold text-ink tracking-[-0.015em]">{meta.t}</h1>
          <span className="text-[12px] text-ink-faint hidden sm:inline truncate">{meta.s}</span>
          <button
            className="theme-toggle ml-auto"
            onClick={() => {
              const next = toggleTheme();
              setTheme(next);
              // 主题偏好持久化到本机设置（可选增强：key 级别仅本机）
              api.settingsSave({ theme: next }).catch(() => undefined);
            }}
            title={theme === "dark" ? "切换到白天模式" : "切换到黑夜模式"}
            aria-label="切换主题"
          >
            {theme === "dark" ? "☀ 白天" : "☾ 黑夜"}
          </button>
        </header>
        <div className="flex-1 overflow-y-auto overscroll-contain px-5 lg:px-7 pb-10 pt-3">
          {/* 路由切换时用 key 重置错误边界：换页就该重新开始，不被上一次的错误卡住 */}
          <ErrorBoundary key={loc.pathname} onHome={() => navigate("/")}>
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/assets" element={<AssetsPage />} />
              <Route path="/scenarios" element={<ScenariosPage />} />
              <Route path="/faultlab" element={<FaultLabPage />} />
              <Route path="/graph" element={<GraphWorkspace />} />
              <Route path="/agent" element={<AgentPage />} />
              <Route path="/settings" element={<SettingsPage />} />
            </Routes>
          </ErrorBoundary>
        </div>
      </main>
    </div>
  );
}
