/** 首次启动引导弹窗（onboarding modal）。
 *
 * 解决的问题：平台首次打开时，用户不知道"能接自己的大模型"这件事，
 * 也不知道 Agent 的能力上限与它是否真的用了 LLM。本弹窗用分步动画把
 * 三件事讲清楚并引导完成：
 *   ① 欢迎 / 为什么可选接 LLM（离线 Mock 也能跑，但接了就真 AI 决策）
 *   ② 选服务商 + 填 API key（兼容阿里云百炼 / DeepSeek / OpenAI 兼容端点）
 *   ③ 一键"测试连接并获取模型"（后端代理 GET {base}/models，真实列表）
 *   ④ 从真实列表选模型 → 保存
 *   ⑤ 完成（置 onboarding_done=true，之后不再自动弹）
 *
 * 诚实边界：
 *   - key 只发给本机后端（POST body 仅本次探测），由后端落 ~/.tcms-ai-platform/settings.json，
 *     响应 / 日志 / 仓库绝不含 key；
 *   - 拉模型失败（端点不支持 GET /models / 网络 / 401）→ 中文提示 + 允许手动输入模型名；
 *   - 跳过按钮 = 本次先不接（离线 Mock 可完整体验），引导完成标记仍为未完成，下次启动再提醒。
 */

import { useEffect, useRef, useState } from "react";
import { api, type SettingsView } from "../api";
import { Tag, StatusDot } from "./ui";

const STEPS = [
  { n: "①", label: "为什么接 AI" },
  { n: "②", label: "填 API Key" },
  { n: "③", label: "获取模型" },
  { n: "④", label: "完成" },
] as const;

type Step = number;

export function OnboardingModal({
  settings,
  onComplete,
  onDismiss,
}: {
  settings: SettingsView;
  onComplete: (s: SettingsView) => void;
  onDismiss: () => void;
}) {
  const [step, setStep] = useState<Step>(0);
  const [provider, setProvider] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [showKey, setShowKey] = useState(false);
  const [probeState, setProbeState] = useState<"idle" | "loading" | "done" | "error">("idle");
  const [probeError, setProbeError] = useState("");
  const [models, setModels] = useState<{ id: string; owned_by?: string }[]>([]);
  const [saving, setSaving] = useState(false);
  const [okMsg, setOkMsg] = useState("");
  const [manualMode, setManualMode] = useState(false);
  // 本地跟踪：本次会话内是否已保存 key（避免每步依赖父级 settings 往返）
  const [hasKeyLocal, setHasKeyLocal] = useState(settings.llm.has_key);
  const hasSavedKey = hasKeyLocal;
  const provs = settings.providers ?? {};
  const stepRef = useRef<number>(0);

  // 切步骤时重播入卡动画（key 变化强制重挂载）
  useEffect(() => {
    stepRef.current = step;
  }, [step]);

  const pickProvider = (k: string) => {
    setProvider(k);
    if (provs[k]) {
      setBaseUrl(provs[k].base_url ?? "");
      setModel(provs[k].model ?? "");
    }
    // 换了服务商/端点 → 旧探测结果作废
    setModels([]);
    setProbeState("idle");
    setProbeError("");
  };

  /** 保存 provider/base_url/key（key 有值才提交，避免每次清掉已存 key）。
   *  只落本机文件、更新本地状态；不触发父级关弹窗（关弹窗仅由 finish 完成）。 */
  const saveCredential = async (): Promise<boolean> => {
    setSaving(true);
    setOkMsg("");
    setProbeError("");
    try {
      const patch: Record<string, string | boolean> = { llm_provider: provider, llm_base_url: baseUrl, llm_model: model };
      if (apiKey.trim()) patch.llm_api_key = apiKey.trim();
      await api.settingsSave(patch);
      setApiKey(""); // 已落本机文件，输入框清空
      if (apiKey.trim()) setHasKeyLocal(true);
      return true;
    } catch (e) {
      setProbeError(String(e as Error).slice(0, 200));
      return false;
    } finally {
      setSaving(false);
    }
  };

  /** ③ 测试连接 + 拉取真实模型列表（显式传本次表单值，key 不落库） */
  const runProbe = async () => {
    if (!apiKey.trim() && !hasSavedKey) {
      setProbeError("先填 API key —— 只有带 key 的请求才能问到你的模型清单");
      setProbeState("error");
      return;
    }
    setProbeState("loading");
    setProbeError("");
    setModels([]);
    try {
      const r = await api.llmModels({ base_url: baseUrl.trim() || undefined, api_key: apiKey.trim() || undefined });
      if (r.ok) {
        setModels(r.models);
        // 若当前 model 为空或不在列表 → 默认选第一个
        if (!model || !r.models.some((m) => m.id === model)) {
          setModel(r.models[0]?.id ?? "");
        }
        setProbeState("done");
      } else {
        setProbeError(r.error ?? "连接失败");
        setProbeState("error");
      }
    } catch (e) {
      setProbeError(String(e as Error).slice(0, 200));
      setProbeState("error");
    }
  };

  /** ③ 完成探测 → 下一步（保存凭据 + 模型） */
  const nextAfterModels = async () => {
    const saved = await saveCredential();
    if (!saved) return;
    setProbeState("idle");
    setStep(3);
  };

  const finish = async () => {
    setSaving(true);
    setOkMsg("");
    try {
      // 确保凭据/模型已保存
      const patch: Record<string, string | boolean> = {};
      if (provider) patch.llm_provider = provider;
      if (baseUrl.trim()) patch.llm_base_url = baseUrl.trim();
      if (model.trim()) patch.llm_model = model.trim();
      if (apiKey.trim()) patch.llm_api_key = apiKey.trim();
      patch.onboarding_done = true;
      const s = await api.settingsSave(patch);
      onComplete(s);
    } catch (e) {
      setOkMsg("");
      setProbeError(String(e as Error).slice(0, 200));
    } finally {
      setSaving(false);
    }
  };

  const canStep2 = Boolean(baseUrl.trim());
  const stepState = (i: number): "done" | "active" | "todo" => (i < step ? "done" : i === step ? "active" : "todo");

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" role="dialog" aria-modal="true" aria-label="首次使用引导">
      {/* 遮罩（点空白不关闭：引导应一次走完或明确跳过） */}
      <div className="absolute inset-0 modal-overlay bg-black/55 backdrop-blur-[2px]" />

      <div className="relative modal-card w-full max-w-[560px] max-h-[90vh] overflow-y-auto panel bg-surface border border-line shadow-2xl">
        {/* 顶栏：品牌 + 步骤条 */}
        <header className="sticky top-0 z-10 bg-surface/95 backdrop-blur border-b border-line-soft px-5 py-3.5">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <span className="text-lg">🚄</span>
              <div>
                <div className="text-[13px] font-semibold text-ink">TCMS × AI · 首次使用引导</div>
                <div className="text-[10.5px] text-ink-faint">30 秒可选配置：把平台接上你自己的大模型</div>
              </div>
            </div>
            <button className="btn-ghost btn-sm shrink-0" onClick={onDismiss} title="先跳过（离线 Mock 也能完整体验；下次启动会再次提醒）">
              跳过
            </button>
          </div>
          {/* 步骤条 */}
          <ol className="flex items-center gap-1 flex-wrap mt-2.5">
            {STEPS.map((s, i) => (
              <li key={s.label} className="flex items-center gap-1">
                {i > 0 && <span className="text-ink-faint mx-0.5 text-xs">→</span>}
                <span
                  className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] border transition-all ${
                    stepState(i) === "done"
                      ? "text-ok border-ok/30 bg-ok/5"
                      : stepState(i) === "active"
                        ? "text-ink border-info/50 bg-info/10"
                        : "text-ink-faint border-line"
                  }`}
                >
                  {stepState(i) === "done" ? (
                    <span className="text-ok">✓</span>
                  ) : stepState(i) === "active" ? (
                    <span className="h-1.5 w-1.5 rounded-full bg-info pulse-dot" />
                  ) : null}
                  {s.n} {s.label}
                </span>
              </li>
            ))}
          </ol>
        </header>

        {/* 步骤体（key=step 触发 modal-card-in 重播） */}
        <div key={step} className="modal-card-in px-5 py-4 space-y-3">
          {step === 0 && (
            <>
              <h3 className="text-[15px] font-semibold text-ink">Agent 已经能干活了——接上你的大模型，它会更"聪明"</h3>
              <div className="space-y-2 text-[12.5px] text-ink-dim leading-5">
                <p>
                  <b className="text-ink">不接也能完整体验</b>：平台内置<b className="text-info">离线 Mock 后端</b>
                  ——Agent 全流程（理解目标 → 检索证据 → 真实执行 → 自证）零成本、可复现地跑给你看。
                </p>
                <p>
                  <b className="text-ink">接上自己的 LLM</b> 后，Agent 的"规划 / 选场景 / 意图解析"由真实大模型决策，
                  输出会标注 <Tag tone="vio">[LLM 模型名]</Tag>。兼容任何 <b className="text-ink">OpenAI 兼容协议</b>
                  端点：阿里云百炼 / DeepSeek / OpenAI / 自建 vLLM 等。
                </p>
              </div>
              <div className="panel bg-surface-2/40 px-3 py-2.5 text-[11.5px] text-ink-dim leading-5 flex items-start gap-2">
                <span className="shrink-0">🔒</span>
                <span>
                  Key 只写入本机设置文件（<code className="kbd-mono">{settings.dir}</code>），
                  请求只发给你填的 base_url —— 平台后端不转发给第三方，仓库/日志绝不含 key。
                </span>
              </div>
              <div className="flex justify-end gap-2 pt-1">
                <button className="btn-ghost" onClick={onDismiss}>
                  先跳过，用离线 Mock
                </button>
                <button className="btn" onClick={() => setStep(1)}>
                  开始配置 →
                </button>
              </div>
            </>
          )}

          {step === 1 && (
            <>
              <h3 className="text-[15px] font-semibold text-ink">选服务商 + 填 API Key</h3>
              <div className="grid gap-2">
                <label className="text-[11px] text-ink-faint">
                  服务商（选预设自动填端点与推荐模型；也可自定义 OpenAI 兼容端点）
                  <select className="select w-full mt-1" value={provider} onChange={(e) => pickProvider(e.target.value)}>
                    <option value="">（自定义 OpenAI 兼容端点）</option>
                    {Object.entries(provs).map(([k, v]) => (
                      <option key={k} value={k}>
                        {v.label}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="text-[11px] text-ink-faint">
                  Base URL（OpenAI 兼容）
                  <input
                    className="input mt-1 font-mono text-[12px]"
                    placeholder="https://api.deepseek.com/v1 或 https://dashscope.aliyuncs.com/compatible-mode/v1"
                    value={baseUrl}
                    onChange={(e) => {
                      setBaseUrl(e.target.value);
                      setModels([]);
                      setProbeState("idle");
                    }}
                  />
                </label>
                <label className="text-[11px] text-ink-faint">
                  API Key
                  <div className="flex gap-2 mt-1">
                    <input
                      className="input font-mono"
                      type={showKey ? "text" : "password"}
                      placeholder={hasSavedKey ? "已保存（可留空，用已存的 key 探测）" : "sk-…"}
                      value={apiKey}
                      onChange={(e) => setApiKey(e.target.value)}
                    />
                    <button className="btn-ghost btn-sm shrink-0" onClick={() => setShowKey((v) => !v)} title={showKey ? "隐藏" : "显示"}>
                      {showKey ? "隐藏" : "显示"}
                    </button>
                  </div>
                </label>
                {hasSavedKey && (
                  <div className="text-[10.5px] text-ok flex items-center gap-1.5">
                    <StatusDot tone="ok" /> 本机已保存 key（{provider || "自定义端点"}），可直接进入下一步
                  </div>
                )}
              </div>
              <div className="flex justify-between pt-1">
                <button className="btn-ghost" onClick={() => setStep(0)}>
                  ← 上一步
                </button>
                <button className="btn" disabled={!canStep2} onClick={() => void saveCredential().then((ok) => ok && setStep(2))} title={canStep2 ? undefined : "先填 Base URL（可直接粘服务商端点）"}>
                  保存并下一步 →
                </button>
              </div>
            </>
          )}

          {step === 2 && (
            <>
              <h3 className="text-[15px] font-semibold text-ink">测试连接 & 获取你的模型列表</h3>
              <p className="text-[12px] text-ink-dim leading-5">
                平台会向 <code className="kbd-mono">{baseUrl || "已配置端点"}</code> 请求 <code className="kbd-mono">GET /models</code>,
                拉出你的账号真实可用的模型——不用手写、不怕拼错。
              </p>

              {probeState === "loading" && (
                <div className="panel bg-surface-2/40 px-4 py-5 text-center space-y-2">
                  <div className="flex justify-center">
                    <span className="h-2 w-2 rounded-full bg-info pulse-dot" />
                  </div>
                  <div className="text-[12.5px] text-ink-dim">正在连接 {baseUrl || "已配置端点"} 并拉取模型…</div>
                  <div className="text-[10.5px] text-ink-faint">通常 1-3 秒；超时自动提示，可转手动输入</div>
                </div>
              )}

              {probeState === "error" && (
                <div className="panel border-bad/30 bg-bad/5 px-3 py-2.5 text-[12px] text-bad leading-5">
                  <b>连接/拉取失败：</b> {probeError}
                  <div className="mt-1 text-ink-dim text-[11px]">检查 base_url 与 key 是否正确；某些端点不支持 GET /models —— 可以手动输入模型名。</div>
                </div>
              )}

              {probeState === "done" && models.length > 0 && (
                <div className="panel border-ok/30 bg-ok/5 px-3 py-3 space-y-2 modal-card-in">
                  <div className="text-[11px] text-ok flex items-center gap-1.5">
                    <StatusDot tone="ok" /> 连接成功，发现 {models.length} 个可用模型：
                  </div>
                  <select className="select w-full" value={model} onChange={(e) => setModel(e.target.value)}>
                    {models.map((m) => (
                      <option key={m.id} value={m.id}>
                        {m.id}
                        {m.owned_by ? `（${m.owned_by}）` : ""}
                      </option>
                    ))}
                  </select>
                  <div className="text-[10.5px] text-ink-faint">
                    Agent 将用 <code className="kbd-mono">{model || "…"}</code> 做规划决策（可随时到「设置」页切换）
                  </div>
                </div>
              )}

              {(probeState === "done" && models.length === 0) || manualMode ? (
                <div className="panel bg-surface-2/40 px-3 py-2.5 space-y-1.5">
                  <div className="text-[11px] text-ink-faint">手动输入模型名（端点不支持 /models 时用）</div>
                  <input className="input font-mono text-[12px]" placeholder="deepseek-chat / deepseek-v3.2 / …" value={model} onChange={(e) => setModel(e.target.value)} />
                </div>
              ) : null}

              <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
                <div className="flex gap-2">
                  <button className="btn-ghost" onClick={() => setStep(1)}>
                    ← 上一步
                  </button>
                  {probeState !== "loading" && (
                    <button className="btn-ghost btn-sm" onClick={() => void runProbe()}>
                      {probeState === "done" ? "↻ 重新获取" : "⚡ 测试连接并获取模型"}
                    </button>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  {(probeState === "error" || (probeState === "done" && models.length === 0)) && (
                    <button className="btn-ghost btn-sm" onClick={() => setManualMode(true)} disabled={manualMode || saving}>
                      改手动输入模型
                    </button>
                  )}
                  <button className="btn" disabled={!model.trim() || saving} onClick={() => void nextAfterModels()}>
                    {saving ? "保存中…" : "用这个模型，下一步 →"}
                  </button>
                </div>
              </div>
            </>
          )}

          {step === 3 && (
            <>
              <div className="text-center py-2 space-y-2 modal-card-in">
                <div className="text-4xl">🎉</div>
                <h3 className="text-[15px] font-semibold text-ink">
                  {provider ? `已接入 ${provs[provider]?.label ?? provider}` : "已接入自定义端点"} · {model}
                </h3>
                <p className="text-[12px] text-ink-dim leading-5 max-w-sm mx-auto">
                  Agent 现在会用 <b className="text-ok">{model}</b> 做规划与意图解析。
                  {okMsg && <span className="block mt-1 text-ok">{okMsg}</span>}
                </p>
                <div className="flex flex-col sm:flex-row items-center justify-center gap-2 pt-2">
                  <button className="btn" onClick={() => void finish()} disabled={saving}>
                    {saving ? "完成中…" : "我完成了，开始使用 →"}
                  </button>
                  {!hasSavedKey && (
                    <button className="btn-ghost" onClick={onDismiss}>
                      稍后再说（下次启动提醒）
                    </button>
                  )}
                </div>
                {!provider && !model && (
                  <p className="text-[10.5px] text-ink-faint">未保存任何配置 —— 直接点「完成」将使用离线 Mock 后端。</p>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
