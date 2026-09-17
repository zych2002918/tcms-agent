import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type SettingsView } from "../api";
import { Panel, Tag, EmptyState, StatusDot } from "../components/ui";

type Sys = {
  engine: { ok: boolean; version?: string; reason?: string };
  llm_key: boolean;
  agent_backend?: string;
  asset_mode: string;
  capabilities?: Record<string, boolean>;
  fix_hints?: { engine: string[]; llm: string[] };
};

const STEP_LABELS = ["① 资产源", "② 接入 AI（可选）", "③ 检查引擎", "④ 完成"];

/** 能力矩阵：机器自证的「当前开哪些能力」（值与 /api/system/status.capabilities 对齐） */
const CAP_ROWS: { k: string; label: string; desc: string; kind: "always" | "engine" | "llm" }[] = [
  { k: "browse_assets", label: "浏览测试资产", desc: "DBC 报文 · 信号 · FMEA 故障 · RTM 需求", kind: "always" },
  { k: "knowledge_graph", label: "知识图谱检索", desc: "向量 + 图谱 + GraphRAG 证据链", kind: "always" },
  { k: "run_scenario", label: "场景执行", desc: "在真实 TCMS 引擎上跑故障场景", kind: "engine" },
  { k: "agent", label: "AI Agent 规划与执行", desc: "检索证据 → 真实执行 → 复盘评分", kind: "engine" },
  { k: "llm_generation", label: "真 LLM 决策 / 生成", desc: "Agent 规划与场景选择由 LLM 完成（可选增强）", kind: "llm" },
];

export function SettingsPage() {
  const [st, setSt] = useState<SettingsView | null>(null);
  const [sys, setSys] = useState<Sys | null>(null);
  const [err, setErr] = useState("");
  const [saving, setSaving] = useState(false);
  const [okMsg, setOkMsg] = useState("");
  const [step, setStep] = useState(0); // 向导步
  // LLM 表单
  const [provider, setProvider] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [showKey, setShowKey] = useState(false);
  // 模型探测（测试连接 + 拉取真实列表）
  const [probeState, setProbeState] = useState<"idle" | "loading" | "done" | "error">("idle");
  const [probeError, setProbeError] = useState("");
  const [models, setModels] = useState<{ id: string; owned_by?: string }[]>([]);
  const [manualMode, setManualMode] = useState(false);
  // 资产目录
  const [assetDir, setAssetDir] = useState("");

  const load = useCallback(async () => {
    try {
      const [s, sy] = await Promise.all([api.settingsGet(), api.systemStatus()]);
      setSt(s);
      setSys(sy);
      setProvider(s.llm.provider || "");
      setBaseUrl(s.llm.base_url || "");
      setModel(s.llm.model || "");
      setAssetDir(s.asset_dir || "");
    } catch (e) {
      setErr(String(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // 选择 provider 预设时填充 base_url/model
  const pickProvider = (p: string) => {
    setProvider(p);
    if (st?.providers?.[p]) {
      setBaseUrl(st.providers[p].base_url || "");
      setModel(st.providers[p].model || "");
    }
    setModels([]);
    setProbeState("idle");
    setProbeError("");
  };

  /** 测试连接 + 拉取真实模型列表（OpenAI 兼容 GET /models；失败可转手动） */
  const runProbe = async () => {
    if (!apiKey.trim() && !st?.llm.has_key) {
      setProbeState("error");
      setProbeError("先填 API key —— 只有带 key 的请求才能问到你的模型清单");
      return;
    }
    setProbeState("loading");
    setProbeError("");
    setModels([]);
    try {
      const r = await api.llmModels({ base_url: baseUrl.trim() || undefined, api_key: apiKey.trim() || undefined });
      if (r.ok) {
        setModels(r.models);
        if (!model || !r.models.some((m) => m.id === model)) {
          setModel(r.models[0]?.id ?? "");
        }
        setProbeState("done");
      } else {
        setProbeState("error");
        setProbeError(r.error ?? "连接失败");
      }
    } catch (e) {
      setProbeState("error");
      setProbeError(String(e as Error).slice(0, 200));
    }
  };

  const saveLlm = async () => {
    setSaving(true);
    setErr("");
    setOkMsg("");
    try {
      const patch: Record<string, string | boolean> = {
        llm_provider: provider,
        llm_base_url: baseUrl,
        llm_model: model,
      };
      // 只在新填 key 时提交（避免每次清掉已存 key）
      if (apiKey.trim()) patch.llm_api_key = apiKey.trim();
      const s = await api.settingsSave(patch);
      setSt(s);
      setApiKey("");
      setOkMsg("AI 配置已保存到本机（key 只存本地文件，不会上传/入库）");
    } catch (e) {
      setErr(String(e as Error).slice(0, 200));
    } finally {
      setSaving(false);
    }
  };

  const saveAsset = async (dir: string) => {
    setSaving(true);
    setErr("");
    setOkMsg("");
    try {
      const s = await api.settingsSave({ asset_dir: dir });
      setSt(s);
      setOkMsg("资产目录设置已保存。重启服务后生效（当前页面数据仍来自原资产源）。");
    } catch (e) {
      setErr(String(e as Error).slice(0, 200));
    } finally {
      setSaving(false);
    }
  };

  const finishOnboarding = async () => {
    setSaving(true);
    try {
      const s = await api.settingsSave({ onboarding_done: true });
      setSt(s);
      setOkMsg("引导完成！你已经可以开始使用了。");
    } catch (e) {
      setErr(String(e));
    } finally {
      setSaving(false);
    }
  };

  const engineOk = sys?.engine.ok ?? true;
  const llmReady = sys?.llm_key ?? false;
  const hasAssetCustom = Boolean(st?.asset_dir);

  // ---- 扩展与集成面板派生数据 ----
  const srcMode = sys?.asset_mode || "";
  const isBundled = srcMode.startsWith("bundled");
  const srcFriendly = isBundled ? "内置快照（未指向外部目录）" : srcMode ? "外部资产目录" : "…";
  const portNow = window.location.port || String(st?.port ?? 8000);
  const provLabel = st?.llm.provider && st?.providers?.[st.llm.provider] ? st.providers[st.llm.provider].label : st?.llm.provider || "";
  const caps = sys?.capabilities ?? {};
  const capVal = (k: string, always: boolean) => (caps[k] === undefined ? always : caps[k]);
  const capMeta = (k: string, v: boolean) => {
    if (v) return { tone: "ok" as const, txt: "已开启" };
    return k === "llm_generation"
      ? { tone: "info" as const, txt: "未开启 · 可选（第②步接入 LLM 后启用）" }
      : { tone: "warn" as const, txt: "未开启 · 需先启用引擎" };
  };
  const envRows: { name: string; role: string; val: string; tone: "ok" | "warn" | "info" }[] = [
    {
      name: "TCMS_UPSTREAM_DIR",
      role: "资产 + 引擎活目录（解析链：env → 设置页资产目录）",
      val: isBundled ? "未设置 → 内置快照" : `当前解析源 → ${srcMode}`,
      tone: isBundled ? "info" : "ok",
    },
    {
      name: "TCMS_AI_HOME",
      role: "本地设置目录（settings.json 所在处）",
      val: st?.dir ?? "~/.tcms-ai-platform",
      tone: "info",
    },
    { name: "PORT", role: "Web 服务端口", val: portNow, tone: "info" },
    {
      name: "LLM_BASE_URL",
      role: "OpenAI 兼容 API 端点",
      val: st?.llm.base_url?.trim() ? st.llm.base_url : "（未设置 → 默认阿里百炼兼容端点）",
      tone: st?.llm.base_url?.trim() ? "info" : "info",
    },
    {
      name: "LLM_MODEL",
      role: "默认模型名",
      val: st?.llm.model?.trim() ? st.llm.model : "（未设置 → 默认 deepseek-v3.2）",
      tone: st?.llm.model?.trim() ? "info" : "info",
    },
    {
      name: "DASH_API_KEY",
      role: "API key 来源：env → 设置文件 → ~/.dsh/.credentials.yaml（refs.ALIYUN_API_KEY）",
      val: llmReady ? "已设置" : "未设置（Agent 自动走离线 Mock，可完整体验）",
      tone: llmReady ? "ok" : "warn",
    },
  ];

  /** 完成步的下一步建议（可点击直达） */
  const nextSteps: { t: string; d: string; to?: string; back?: number }[] = [
    { t: "▶ 故障演示", d: "选一个真实故障，看它如何被检测与处置", to: "/faultlab" },
    { t: "◈ 知识图谱", d: "用大白话问 TCMS 领域知识，看证据链", to: "/graph" },
    { t: "✦ AI Agent · 自由目标", d: "给 Agent 一个任务/目标，看它检索证据并真实执行", to: "/agent" },
    { t: "▤ 测试资产", d: "浏览 DBC 报文 / 故障字典 / 安全需求", to: "/assets" },
    { t: "⚙ 自定义场景", d: "手动编排故障场景，在 TCMS 引擎上真实执行", to: "/scenarios" },
    { t: "⇄ 接入自有数据 / AI", d: "回到第①②步：接资产目录、填 API key", back: 0 },
  ];

  return (
    <div className="mx-auto w-full max-w-[1720px] space-y-4">
      {/* 顶部状态行 */}
      {st && (
        <div className="flex flex-wrap items-center gap-x-5 gap-y-1.5 text-[12px] text-ink-dim">
          <span>
            <StatusDot tone={engineOk ? "ok" : "warn"} pulse={!engineOk} /> 引擎 {engineOk ? `v${sys?.engine.version ?? ""}` : "未启用"}
          </span>
          <span>
            <StatusDot tone={llmReady ? "ok" : "info"} /> AI 后端 {llmReady ? "LLM" : "Mock（离线）"}
          </span>
          <span className="text-ink-faint">
            设置目录：<code className="kbd-mono">{st.dir}</code>
          </span>
        </div>
      )}

      {err && <div className="panel border-bad/40 bg-bad/10 px-4 py-2.5 text-sm text-bad">⚠ {err}</div>}
      {okMsg && <div className="panel border-ok/40 bg-ok/10 px-4 py-2.5 text-sm text-ok">✓ {okMsg}</div>}

      {/* ============ 新手引导向导 ============ */}
      <Panel
        title="新手引导（按顺序走一遍即可上手）"
        right={st?.onboarding_done ? <Tag tone="ok">已完成</Tag> : <Tag tone="warn">未完成</Tag>}
        bodyClass="p-4"
      >
        {/* 步骤条 */}
        <div className="flex flex-wrap items-center gap-1 mb-4">
          {STEP_LABELS.map((l, i) => (
            <span key={l} className="flex items-center gap-1">
              {i > 0 && <span className="text-ink-faint mx-0.5 text-xs">→</span>}
              <button
                onClick={() => setStep(i)}
                className={`rounded-full px-2.5 py-1 text-xs border transition-colors ${
                  step === i ? "text-ink border-info/50 bg-info/10" : i < step ? "text-ok border-ok/30 bg-ok/5" : "text-ink-faint border-line"
                }`}
              >
                {i < step && <span className="text-ok">✓ </span>}
                {l}
              </button>
            </span>
          ))}
        </div>

        {/* 第 0 步：资产源 */}
        {step === 0 && (
          <div className="space-y-3">
            <div className="text-[13px] leading-6">
              平台的测试资产（报文 / 故障 / 场景 / 需求）来自 <b className="text-ink">TCMS 引擎目录</b>。
              <div className="text-ink-dim text-[12px] mt-1">
                当前模式：<code className="kbd-mono">{sys?.asset_mode ?? "…"}</code>
                {!hasAssetCustom && "（内置快照已够上手；接自己的资产目录即可扩展到真实数据）"}
              </div>
            </div>
            <div className="panel bg-surface-2/40 p-3 space-y-2">
              <div className="text-[12px] text-ink-dim">
                想用自己的实际数据？例如：往 tcms-can-test 的 <code className="kbd-mono">tcms/faults.yaml</code> 加故障、往
                <code className="kbd-mono">scenarios/</code> 加场景、往 <code className="kbd-mono">tcms.dbc</code> 加报文——把你的
                tcms-can-test 目录路径填到下面，重启后平台启动时自动扫描加载：
              </div>
              <div className="flex flex-col sm:flex-row gap-2">
                <input
                  className="input flex-1 font-mono text-[12px]"
                  placeholder="如 D:\my-tcms\tcms-can-test"
                  value={assetDir}
                  onChange={(e) => setAssetDir(e.target.value)}
                />
                <button className="btn btn-sm justify-center" onClick={() => saveAsset(assetDir)} disabled={saving}>
                  保存资产目录
                </button>
              </div>
              {hasAssetCustom && (
                <div className="text-[11px] text-warn">
                  ⚠ 已设置自定义目录（重启后生效）。想回到内置快照？清空输入框后点「保存」。
                </div>
              )}
            </div>
            <div className="flex justify-end">
              <button className="btn" onClick={() => setStep(1)}>下一步：接入 AI →</button>
            </div>
          </div>
        )}

        {/* 第 1 步：LLM / API */}
        {step === 1 && (
          <div className="space-y-3">
            <div className="text-[13px] leading-6">
              想让 Agent 用<b className="text-ink">真 LLM 做规划决策</b>（而非离线规则）？接入任意 OpenAI 兼容端点即可（阿里云百炼 / DeepSeek / OpenAI / 自建）。
              <div className="text-ink-dim text-[12px] mt-0.5">
                不填也能完整体验：Agent 自动走离线 Mock 后端——全流程可演示、零成本、不卡壳。
              </div>
            </div>
            <div className="grid sm:grid-cols-2 gap-2">
              <label className="text-[11px] text-ink-faint">服务商（选预设自动填 base_url/model）
                <select className="select w-full mt-1" value={provider} onChange={(e) => pickProvider(e.target.value)}>
                  <option value="">（自定义 / 接入你自己的模型）</option>
                  {st?.providers &&
                    Object.entries(st.providers).map(([k, v]) => (
                      <option key={k} value={k}>
                        {v.label}
                      </option>
                    ))}
                </select>
              </label>
              <label className="text-[11px] text-ink-faint">API Key
                <input
                  className="input mt-1 font-mono"
                  type={showKey ? "text" : "password"}
                  placeholder={st?.llm.has_key ? "已保存（留空保持不变）" : "sk-…"}
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                />
              </label>
              <label className="text-[11px] text-ink-faint">Base URL
                <input className="input mt-1 font-mono text-[12px]" placeholder="https://…/v1" value={baseUrl} onChange={(e) => { setBaseUrl(e.target.value); setModels([]); setProbeState("idle"); }} />
              </label>
              <div className="text-[11px] text-ink-faint">
                模型
                {manualMode ? (
                  <input
                    className="input mt-1 font-mono"
                    placeholder="deepseek-v3.2 / deepseek-chat / …"
                    value={model}
                    onChange={(e) => setModel(e.target.value)}
                  />
                ) : (
                  <>
                    <select className="select w-full mt-1" value={model} onChange={(e) => setModel(e.target.value)} disabled={models.length === 0 && probeState !== "done"}>
                      {models.length === 0 ? (
                        <option value="">（点下方「测试连接并获取模型」拉取真实列表，或手动输入）</option>
                      ) : (
                        models.map((m) => (
                          <option key={m.id} value={m.id}>
                            {m.id}
                            {m.owned_by ? `（${m.owned_by}）` : ""}
                          </option>
                        ))
                      )}
                    </select>
                    {probeState === "error" && <div className="mt-1 text-[11px] text-bad">{probeError}</div>}
                    <div className="flex gap-2 mt-1.5 flex-wrap">
                      <button className="btn-ghost btn-sm" onClick={() => void runProbe()} disabled={probeState === "loading"}>
                        {probeState === "loading" ? "连接中…" : probeState === "done" ? "↻ 重新获取模型" : "⚡ 测试连接并获取模型"}
                      </button>
                      {probeState === "error" && (
                        <button className="btn-ghost btn-sm" onClick={() => setManualMode(true)}>
                          改手动输入模型名
                        </button>
                      )}
                    </div>
                  </>
                )}
                {manualMode && (
                  <button className="btn-ghost btn-sm mt-1" onClick={() => setManualMode(false)}>
                    改回自动获取
                  </button>
                )}
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <button className="btn btn-sm" onClick={saveLlm} disabled={saving}>保存 AI 配置</button>
              <label className="flex items-center gap-1.5 text-[11px] text-ink-faint cursor-pointer">
                <input type="checkbox" checked={showKey} onChange={(e) => setShowKey(e.target.checked)} /> 显示 key
              </label>
              {st?.llm.has_key && (
                <button className="btn-ghost btn-sm" onClick={async () => { await api.settingsClearApiKey().then(setSt); setOkMsg("已清除本机保存的 API key"); }} disabled={saving}>
                  清除已存 key
                </button>
              )}
              <span className="ml-auto text-[10px] text-ink-faint">key 只写入本机 {st?.dir ?? "~/.tcms-ai-platform/settings.json"}，绝不上传/入库</span>
            </div>
            <div className="flex justify-between">
              <button className="btn-ghost" onClick={() => setStep(0)}>← 上一步</button>
              <button className="btn" onClick={() => setStep(2)}>下一步：检查引擎 →</button>
            </div>
          </div>
        )}

        {/* 第 2 步：引擎检查 */}
        {step === 2 && (
          <div className="space-y-3">
            <div className="text-[13px] leading-6">
              场景真实执行 / Agent 需要 <b className="text-ink">TCMS 引擎</b>（tcms-can-test）。
            </div>
            <div className="panel bg-surface-2/40 p-3 space-y-1.5 text-[13px]">
              <div className="flex items-center gap-2">
                <StatusDot tone={engineOk ? "ok" : "warn"} pulse={!engineOk} />
                TCMS 引擎：{engineOk ? `可用（v${sys?.engine.version ?? ""}）` : "不可用"}
              </div>
              <div className="flex items-center gap-2">
                <StatusDot tone={llmReady ? "ok" : "info"} />
                AI 后端：{llmReady ? "LLM（已配 key）" : "Mock（离线，可完整演示）"}
              </div>
            </div>
            {!engineOk && (
              <div className="text-[12px] text-ink-dim leading-5 panel border-warn/30 bg-warn/5 p-3">
                引擎不可用时：资产浏览、知识图谱、故障演示仍完整可用；场景执行与 Agent 会提示引导。
                <div className="mt-1.5 text-warn">启用方法：在终端跑 <code className="kbd-mono">pip install -e ".[upstream]"</code> 或把资产目录指向已安装 tcms-can-test 的路径（见第①步）。</div>
              </div>
            )}
            <div className="flex justify-between">
              <button className="btn-ghost" onClick={() => setStep(1)}>← 上一步</button>
              <button className="btn" onClick={() => setStep(3)}>下一步：完成 →</button>
            </div>
          </div>
        )}

        {/* 第 3 步：完成 */}
        {step === 3 && (
          <div className="space-y-3">
            <div className="text-[13px] leading-6">
              都设置好了。接下来你可以（点卡片直达）：
            </div>
            <div className="grid sm:grid-cols-2 xl:grid-cols-3 gap-2 text-[12px]">
              {nextSteps.map((n) => {
                const inner = (
                  <>
                    <div className="font-medium text-ink">{n.t}</div>
                    <div className="text-ink-dim mt-0.5 text-xs">{n.d}</div>
                  </>
                );
                return n.to ? (
                  <Link key={n.t} to={n.to} className="panel bg-surface-2/40 px-3 py-2.5 block hover:border-info/40 transition-colors">
                    {inner}
                  </Link>
                ) : (
                  <button
                    key={n.t}
                    onClick={() => setStep(n.back ?? 0)}
                    className="panel bg-surface-2/40 px-3 py-2.5 text-left w-full hover:border-info/40 transition-colors cursor-pointer"
                  >
                    {inner}
                  </button>
                );
              })}
            </div>
            <div className="flex justify-between items-center">
              <button className="btn-ghost" onClick={() => setStep(2)}>← 上一步</button>
              {!st?.onboarding_done ? (
                <button className="btn" onClick={finishOnboarding} disabled={saving}>
                  我完成了，开始使用 →
                </button>
              ) : (
                <Tag tone="ok">引导已完成</Tag>
              )}
            </div>
          </div>
        )}
      </Panel>

      {/* ============ 扩展与集成：可扩展点地图 ============ */}
      <Panel
        title="扩展与集成 · 外部可配置接口（给开发者 / 现场人员）"
        right={<Tag tone="vio">免改代码 · 3 类扩展点</Tag>}
        bodyClass="p-4"
      >
        <p className="text-[12px] text-ink-dim leading-5 mb-3">
          把这个平台当开源项目用：<b className="text-ink">内容、模型、运行参数</b> 三类扩展点都开放成外部接口——
          不 fork、不改平台代码即可接入自己的东西。取参优先级：<b className="text-ink">环境变量 &gt; 设置文件 &gt; 内置默认</b>；
          设置文件在 <code className="kbd-mono">{st?.dir ?? "~/.tcms-ai-platform/settings.json"}</code>，不进仓库。
        </p>

        {/* 三卡：数据 / AI / 环境 */}
        <div className="grid md:grid-cols-3 gap-2.5">
          {/* ① 数据扩展 */}
          <div className="panel bg-surface-2/40 p-3 flex flex-col gap-2">
            <div className="flex items-center justify-between">
              <span className="text-[13px] font-semibold text-ink">① 数据扩展</span>
              <Tag tone="dim">assets</Tag>
            </div>
            <div className="text-[11.5px] text-ink-dim leading-5">
              自定义资产 = 往你的 tcms-can-test 目录加内容，重启后启动时自动加载：
              <div className="mt-1.5 space-y-0.5">
                <div>· 报文 → <code className="kbd-mono">tcms/tcms.dbc</code></div>
                <div>· 故障 → <code className="kbd-mono">tcms/faults.yaml</code>（加条目 + 处置）</div>
                <div>· 场景 → <code className="kbd-mono">scenarios/</code>（放一个 yaml）</div>
              </div>
            </div>
            <div className="mt-auto pt-1 border-t border-line-soft/70">
              <div className="flex items-center gap-2 text-[11.5px]">
                <span className="text-ink-faint">资产源目录已指向？</span>
                <StatusDot tone={isBundled ? "info" : "ok"} />
                <span className={isBundled ? "text-ink-dim" : "text-ok"}>{srcFriendly}</span>
              </div>
              <Link to="/assets" className="text-info text-xs hover:underline inline-block mt-1.5">
                去资产页看效果 →
              </Link>
            </div>
          </div>

          {/* ② AI / API 扩展 */}
          <div className="panel bg-surface-2/40 p-3 flex flex-col gap-2">
            <div className="flex items-center justify-between">
              <span className="text-[13px] font-semibold text-ink">② AI / API 扩展</span>
              <Tag tone="dim">llm</Tag>
            </div>
            <div className="text-[11.5px] text-ink-dim leading-5 space-y-1">
              <div>
                服务商：<code className="kbd-mono">{provLabel || "（未选择 — 任意 OpenAI 兼容）"}</code>
              </div>
              <div>
                模型：<code className="kbd-mono">{st?.llm.model?.trim() || "（默认 deepseek-v3.2）"}</code>
              </div>
              <div>
                Base URL：
                <code className="kbd-mono">{st?.llm.base_url?.trim() || "（默认阿里百炼兼容端点）"}</code>
              </div>
              <div className="flex items-center gap-1.5">
                Key：
                <StatusDot tone={llmReady ? "ok" : "info"} />
                {llmReady ? "已设置" : "未设置（离线 Mock 可完整体验）"}
              </div>
            </div>
            <div className="mt-auto pt-1 border-t border-line-soft/70">
              <button className="btn-ghost btn-sm" onClick={() => setStep(1)}>在此接入你自己的模型 / 填 key →</button>
              <div className="text-[10px] text-ink-faint mt-1">兼容：阿里云百炼 · DeepSeek · OpenAI · 自建 v1 端点</div>
            </div>
          </div>

          {/* ③ 环境变量扩展 */}
          <div className="panel bg-surface-2/40 p-3 flex flex-col gap-2">
            <div className="flex items-center justify-between">
              <span className="text-[13px] font-semibold text-ink">③ 环境变量扩展</span>
              <Tag tone="dim">env</Tag>
            </div>
            <div className="text-[11.5px] text-ink-dim leading-5">
              启动服务前设置的环境变量优先级最高，适合部署 / 现场，无需动设置文件：
              <div className="mt-1.5 flex flex-wrap gap-1">
                {["TCMS_UPSTREAM_DIR", "TCMS_AI_HOME", "PORT", "LLM_BASE_URL", "LLM_MODEL", "DASH_API_KEY"].map((v) => (
                  <code key={v} className="kbd-mono text-[10.5px] bg-surface px-1.5 py-0.5 rounded border border-line-soft">
                    {v}
                  </code>
                ))}
              </div>
            </div>
            <div className="mt-auto pt-1 border-t border-line-soft/70 text-[11.5px] text-ink-faint">
              当前取值与状态见下方「环境变量键值表」↓
            </div>
          </div>
        </div>

        {/* 能力矩阵 */}
        <div className="mt-4 pt-3 border-t border-line-soft">
          <div className="flex items-center justify-between mb-2">
            <span className="section-title">能力矩阵 · 当前开哪些能力</span>
            <Tag tone="ok">机器自证</Tag>
          </div>
          <div className="table-scroll">
            <table style={{ minWidth: 620 }}>
              <thead>
                <tr>
                  <th className="th">能力</th>
                  <th className="th">名称</th>
                  <th className="th">说明</th>
                  <th className="th">当前状态</th>
                </tr>
              </thead>
              <tbody>
                {CAP_ROWS.map((r) => {
                  const v = capVal(r.k, r.kind === "always");
                  const meta = capMeta(r.k, v);
                  return (
                    <tr key={r.k} className="tr-hover">
                      <td className="td">
                        <code className="kbd-mono">{r.k}</code>
                      </td>
                      <td className="td text-ink font-medium">{r.label}</td>
                      <td className="td text-ink-dim text-[12.5px]">{r.desc}</td>
                      <td className="td">
                        <span className="inline-flex items-center gap-1.5 text-[12px]">
                          <StatusDot tone={meta.tone} />
                          <span className={meta.tone === "ok" ? "text-ok" : meta.tone === "warn" ? "text-warn" : "text-ink-dim"}>
                            {meta.txt}
                          </span>
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>

        {/* 环境变量键值表 */}
        <div className="mt-4 pt-3 border-t border-line-soft">
          <div className="flex items-center justify-between mb-2">
            <span className="section-title">环境变量键值表</span>
            <Tag tone="dim">当前值 / 状态（只读）</Tag>
          </div>
          <div className="table-scroll">
            <table style={{ minWidth: 720 }}>
              <thead>
                <tr>
                  <th className="th">环境变量</th>
                  <th className="th">作用</th>
                  <th className="th">当前值 / 状态</th>
                </tr>
              </thead>
              <tbody>
                {envRows.map((r) => (
                  <tr key={r.name} className="tr-hover">
                    <td className="td">
                      <code className="kbd-mono text-[12px]">{r.name}</code>
                    </td>
                    <td className="td text-ink-dim text-[12.5px]">{r.role}</td>
                    <td className="td">
                      <span className="inline-flex items-center gap-2 text-[12px]">
                        <StatusDot tone={r.tone} />
                        <span className={r.tone === "warn" ? "text-warn" : r.tone === "ok" ? "text-ok" : "text-ink-dim"}>{r.val}</span>
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-[11px] text-ink-faint mt-2">
            API key 永不回显：只显示「已设置 / 未设置」。来源链：环境变量（DASH_API_KEY / DEEPSEEK_API_KEY /
            OPENAI_API_KEY）→ 设置文件 → ~/.dsh/.credentials.yaml。
          </p>
        </div>
      </Panel>

      {!st && !err && (
        <Panel><EmptyState icon="⚙" title="加载设置中…" desc="正在读取本地配置。" /></Panel>
      )}
    </div>
  );
}
