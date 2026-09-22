import { type ReactNode, useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type SettingsView } from "../api";
import { Callout, EmptyState, KV, Panel, StatusDot, Tag } from "../components/ui";
import {
  IconBolt,
  IconCheck,
  IconGear,
  IconGraph,
  IconInfo,
  IconList,
  IconPlay,
  IconReplay,
  IconSpark,
  IconSwap,
  IconWarn,
} from "../components/icons";

type Sys = {
  engine: { ok: boolean; version?: string; reason?: string };
  llm_key: boolean;
  agent_backend?: string;
  asset_mode: string;
  capabilities?: Record<string, boolean>;
  fix_hints?: { engine: string[]; llm: string[] };
};

/** 向导步骤（顺序与名称是导航/文档/测试对齐的口径，不要改） */
const STEP_LABELS = ["① 资产源", "② 接入 AI（可选）", "③ 检查引擎", "④ 完成"];

/** 步骤序号用具名常量：页内「去第②步填 key」这类跳转才不会指错步（裸数字最容易错位） */
const STEP = { ASSETS: 0, LLM: 1, ENGINE: 2, DONE: 3 } as const;

/** 能力矩阵：机器自证的「当前开哪些能力」（值与 /api/system/status.capabilities 对齐） */
const CAP_ROWS: { k: string; label: string; desc: string; kind: "always" | "engine" | "llm" }[] = [
  { k: "browse_assets", label: "浏览测试资产", desc: "DBC 报文 · 信号 · FMEA 故障 · RTM 需求", kind: "always" },
  { k: "knowledge_graph", label: "知识图谱检索", desc: "向量 + 图谱 + GraphRAG 证据链", kind: "always" },
  { k: "run_scenario", label: "场景执行", desc: "在真实 TCMS 引擎上跑故障场景", kind: "engine" },
  { k: "agent", label: "AI Agent 规划与执行", desc: "检索证据 → 真实执行 → 复盘评分", kind: "engine" },
  { k: "llm_generation", label: "真 LLM 决策 / 生成", desc: "Agent 规划与场景选择由 LLM 完成（可选增强）", kind: "llm" },
];

/** 依赖口径：颜色不表意，用文字说清"这项为什么开/不开"（色盲也能读） */
const CAP_DEP: Record<"always" | "engine" | "llm", string> = {
  always: "内置能力",
  engine: "需 TCMS 引擎",
  llm: "可选（接入 LLM）",
};

/** 就地反馈：保存这类动作的结果必须出现在**动作发生的地方**，不能只在页顶飘一条 */
function InlineNote({ tone, children }: { tone: "ok" | "bad" | "info" | "warn"; children: ReactNode }) {
  const cls = {
    ok: "text-ok",
    bad: "text-bad",
    warn: "text-warn",
    info: "text-ink-dim",
  }[tone];
  const icon = {
    ok: <IconCheck className="h-3.5 w-3.5" />,
    bad: <IconWarn className="h-3.5 w-3.5" />,
    warn: <IconWarn className="h-3.5 w-3.5" />,
    info: <IconInfo className="h-3.5 w-3.5" />,
  }[tone];
  return (
    <div className={`flex items-start gap-1.5 text-[11.5px] leading-5 ${cls}`} role="status" aria-live="polite">
      <span className="inline-flex shrink-0 mt-0.5">{icon}</span>
      <span className="min-w-0">{children}</span>
    </div>
  );
}

export function SettingsPage() {
  const [st, setSt] = useState<SettingsView | null>(null);
  const [sys, setSys] = useState<Sys | null>(null);
  const [err, setErr] = useState("");
  /** 状态接口（/api/system/status）自己失败时的原因：不拖黑整页，只把"状态"如实标成未知 */
  const [sysErr, setSysErr] = useState("");
  const [saving, setSaving] = useState(false);
  /** 就地反馈：哪一处动作、成功还是失败、说了什么（取代原来页顶那条"飘着"的提示） */
  const [note, setNote] = useState<{ at: "llm" | "asset" | "onboarding"; tone: "ok" | "bad"; text: string } | null>(
    null,
  );
  /** 选服务商预设后自动填了什么（字段被程序改过，必须让用户看见） */
  const [presetNote, setPresetNote] = useState("");
  const [step, setStep] = useState<number>(STEP.ASSETS); // 向导步
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
    // 两个接口各自结算：状态接口挂了不该把"能看的设置"一起拖黑
    // （原来用 Promise.all，一个 500 会让整页变成一条错误横幅 + 空表单）
    const [sRes, syRes] = await Promise.allSettled([api.settingsGet(), api.systemStatus()]);
    if (sRes.status === "fulfilled") {
      const s = sRes.value;
      setSt(s);
      setProvider(s.llm.provider || "");
      setBaseUrl(s.llm.base_url || "");
      setModel(s.llm.model || "");
      setAssetDir(s.asset_dir || "");
    } else {
      setErr(`读取本机设置失败：${String(sRes.reason)}`);
    }
    if (syRes.status === "fulfilled") {
      setSys(syRes.value);
      setSysErr("");
    } else {
      setSysErr(String(syRes.reason).slice(0, 160));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // 选择 provider 预设时填充 base_url/model（并如实告诉用户"这两个框被自动改了"）
  const pickProvider = (p: string) => {
    setProvider(p);
    const preset = st?.providers?.[p];
    if (preset) {
      setBaseUrl(preset.base_url || "");
      setModel(preset.model || "");
    }
    setModels([]);
    setProbeState("idle");
    setProbeError("");
    setPresetNote(
      preset
        ? `已按「${preset.label}」预设自动填入 Base URL 与推荐模型（下面两个输入框已更新），可以直接改。`
        : "已切到自定义端点：Base URL 与模型请自己填，或点「测试连接并获取模型」拉取真实清单。",
    );
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
    setNote(null);
    const submittedKey = Boolean(apiKey.trim()); // 留空 = 不修改已保存的 key
    try {
      const patch: Record<string, string | boolean> = {
        llm_provider: provider,
        llm_base_url: baseUrl,
        llm_model: model,
      };
      // 只在新填 key 时提交（避免每次清掉已存 key）
      if (submittedKey) patch.llm_api_key = apiKey.trim();
      const s = await api.settingsSave(patch);
      setSt(s);
      setApiKey("");
      setNote({
        at: "llm",
        tone: "ok",
        text: submittedKey
          ? "AI 配置已保存（含这次新填的 key）。key 只写入本机设置文件，不上传、不入库。"
          : "AI 配置已保存；key 输入框是空的，所以本机已存的 key 没有被改动。",
      });
    } catch (e) {
      setNote({ at: "llm", tone: "bad", text: `保存失败：${String(e as Error).slice(0, 200)}` });
    } finally {
      setSaving(false);
    }
  };

  /** 清除本机保存的 key（原来只 setOkMsg，失败时用户什么都看不到） */
  const clearKey = async () => {
    setErr("");
    setNote(null);
    try {
      const s = await api.settingsClearApiKey();
      setSt(s);
      setNote({ at: "llm", tone: "ok", text: "已清除本机保存的 API key（Agent 会回到离线规则决策）。" });
    } catch (e) {
      setNote({ at: "llm", tone: "bad", text: `清除失败：${String(e as Error).slice(0, 200)}` });
    }
  };

  const saveAsset = async (dir: string) => {
    setSaving(true);
    setErr("");
    setNote(null);
    try {
      const s = await api.settingsSave({ asset_dir: dir });
      setSt(s);
      setNote({
        at: "asset",
        tone: "ok",
        text: "资产目录已保存。重启服务后生效（当前页面数据仍来自原资产源）。",
      });
    } catch (e) {
      setNote({ at: "asset", tone: "bad", text: `保存失败：${String(e as Error).slice(0, 200)}` });
    } finally {
      setSaving(false);
    }
  };

  const finishOnboarding = async () => {
    setSaving(true);
    setErr("");
    setNote(null);
    try {
      const s = await api.settingsSave({ onboarding_done: true });
      setSt(s);
      setNote({ at: "onboarding", tone: "ok", text: "引导已完成，可以开始使用了。" });
    } catch (e) {
      setNote({ at: "onboarding", tone: "bad", text: `保存失败：${String(e as Error).slice(0, 200)}` });
    } finally {
      setSaving(false);
    }
  };

  const engineOk = sys?.engine.ok ?? true;
  const llmReady = sys?.llm_key ?? false;
  const hasAssetCustom = Boolean(st?.asset_dir);
  /** 引擎/AI/资产源这些"运行状态"是否真的读到了（读不到就如实说未知，不猜） */
  const statusKnown = Boolean(sys) && !sysErr;

  // ---- 环境事实 / 外部接口派生数据 ----
  const srcMode = sys?.asset_mode || "";
  const isBundled = srcMode.startsWith("bundled");
  const srcFriendly = statusKnown
    ? isBundled
      ? "内置快照（未指向外部目录）"
      : srcMode
        ? "外部资产目录"
        : "未知"
    : "未知（状态接口不可用）";
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
      tone: "info",
    },
    {
      name: "LLM_MODEL",
      role: "默认模型名",
      val: st?.llm.model?.trim() ? st.llm.model : "（未设置 → 默认 deepseek-v3.2）",
      tone: "info",
    },
    {
      name: "DASH_API_KEY",
      role: "API key 来源：env → 设置文件 → ~/.dsh/.credentials.yaml（refs.ALIYUN_API_KEY）",
      val: llmReady ? "已设置" : "未设置（Agent 自动走离线 Mock，可完整体验）",
      tone: llmReady ? "ok" : "warn",
    },
  ];

  /** 完成步的下一步建议（可点击直达） */
  const nextSteps: { t: string; d: string; icon?: ReactNode; to?: string; back?: number }[] = [
    { t: "故障演示", d: "选一个真实故障，看它如何被检测与处置", icon: <IconPlay className="h-4 w-4" />, to: "/faultlab" },
    { t: "知识图谱", d: "用大白话问 TCMS 领域知识，看证据链", icon: <IconGraph className="h-4 w-4" />, to: "/graph" },
    { t: "AI Agent · 自由目标", d: "给 Agent 一个任务/目标，看它检索证据并真实执行", icon: <IconSpark className="h-4 w-4" />, to: "/agent" },
    { t: "测试资产", d: "浏览 DBC 报文 / 故障字典 / 安全需求", icon: <IconList className="h-4 w-4" />, to: "/assets" },
    { t: "自定义场景", d: "手动编排故障场景，在 TCMS 引擎上真实执行", icon: <IconGear className="h-4 w-4" />, to: "/scenarios" },
    { t: "接入自有数据 / AI", d: "回到第①②步：接资产目录、填 API key", icon: <IconSwap className="h-4 w-4" />, back: STEP.ASSETS },
  ];

  return (
    <div className="mx-auto w-full max-w-[1800px] space-y-4">
      {/* ============ 运行环境（一眼看清现在是什么状态；路径走键值行，不塞进正文） ============ */}
      {(st || sys) && (
        <Panel dense title="运行环境" sub="本机实况 · 只读" bodyClass="p-3">
          <div className="grid gap-x-6 gap-y-1.5 sm:grid-cols-2">
            <div className="flex items-center gap-2 text-[12px] min-w-0">
              <StatusDot tone={!statusKnown ? "warn" : engineOk ? "ok" : "warn"} pulse={statusKnown && !engineOk} />
              <span className="text-ink-dim shrink-0">TCMS 引擎</span>
              <span className={`truncate ${!statusKnown || engineOk ? "text-ink" : "text-warn"}`}>
                {!statusKnown
                  ? "状态未知（状态接口暂不可用）"
                  : engineOk
                    ? `可用（v${sys?.engine.version ?? ""}）`
                    : "未启用 · 场景执行与 Agent 需要它"}
              </span>
            </div>
            <div className="flex items-center gap-2 text-[12px] min-w-0">
              <StatusDot tone={!statusKnown ? "warn" : llmReady ? "ok" : "info"} />
              <span className="text-ink-dim shrink-0">AI 后端</span>
              <span className="text-ink truncate">
                {!statusKnown
                  ? "状态未知（状态接口暂不可用）"
                  : llmReady
                    ? `LLM（${provLabel || "自定义端点"}）`
                    : "Mock（离线规则，可完整体验）"}
              </span>
            </div>
            <KV k="设置目录" v={st?.dir ?? "未读到"} mono />
            <KV k="资产源" v={srcFriendly} mono />
          </div>
          {sysErr && (
            <div className="mt-2">
              <InlineNote tone="warn">
                状态接口（/api/system/status）这次没答上来，所以引擎与资产源显示"未知"；设置本身照常可看可改。
                点此刷新页面可重试。
              </InlineNote>
            </div>
          )}
        </Panel>
      )}

      {err && <div className="panel border-bad/40 bg-bad/10 px-4 py-2.5 text-sm text-bad">⚠ {err}</div>}

      {/* ============ 唯一的主线：新手引导向导 ============ */}
      <Panel
        title="新手引导"
        sub="按顺序走一遍即可上手；每一步都能点回去改"
        right={st?.onboarding_done ? <Tag tone="ok">已完成</Tag> : <Tag tone="warn">未完成</Tag>}
        bodyClass="p-4"
      >
        {/* 步骤条：可点跳步（视觉与 StepFlow 一致：✓ 已完成 / 脉冲点 当前 / 灰 未到） */}
        <ol className="flex flex-wrap items-center gap-1 mb-4">
          {STEP_LABELS.map((l, i) => (
            <li key={l} className="flex items-center gap-1">
              {i > 0 && <span className="text-ink-faint mx-0.5 text-xs">→</span>}
              <button
                onClick={() => setStep(i)}
                aria-current={step === i ? "step" : undefined}
                className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs border transition-colors ${
                  step === i
                    ? "text-ink border-info/50 bg-info/10"
                    : i < step
                      ? "text-ok border-ok/30 bg-ok/5"
                      : "text-ink-faint border-line"
                }`}
              >
                {i < step && <span className="text-ok">✓</span>}
                {step === i && <span className="h-1.5 w-1.5 rounded-full bg-info pulse-dot" />}
                {l}
              </button>
            </li>
          ))}
        </ol>

        {/* 第 0 步：资产源 */}
        {step === STEP.ASSETS && (
          <div className="space-y-3">
            <div className="text-[13px] leading-6">
              平台的测试资产（报文 / 故障 / 场景 / 需求）来自 <b className="text-ink">TCMS 引擎目录</b>；
              不接自己的数据也能用，内置快照已经够上手。
            </div>

            <div className="rounded-[var(--radius-md)] border border-line-soft bg-surface-2/40 p-3 space-y-2">
              <div className="flex items-center gap-2 text-[12px] min-w-0">
                <StatusDot tone={isBundled ? "info" : "ok"} />
                <span className="text-ink-dim shrink-0">当前资产源</span>
                <span className="text-ink">{srcFriendly}</span>
                <code className="kbd-mono ml-auto truncate min-w-0" title={sys?.asset_mode ?? ""}>
                  {sys?.asset_mode ?? "…"}
                </code>
              </div>
              <label className="block text-[11px] text-ink-faint">
                换成你自己的 tcms-can-test 目录（保存后需重启服务才生效）
                <div className="mt-1 flex flex-col sm:flex-row gap-2">
                  <input
                    className="input flex-1 font-mono text-[12px]"
                    placeholder="如 D:\my-tcms\tcms-can-test"
                    value={assetDir}
                    onChange={(e) => setAssetDir(e.target.value)}
                  />
                  <button
                    className="btn btn-sm justify-center whitespace-nowrap"
                    onClick={() => void saveAsset(assetDir)}
                    disabled={saving}
                  >
                    保存资产目录
                  </button>
                </div>
              </label>
              {hasAssetCustom && (
                <InlineNote tone="warn">
                  已设置自定义目录（重启后生效）。想回到内置快照：清空输入框后点「保存」。
                </InlineNote>
              )}
              {note?.at === "asset" && <InlineNote tone={note.tone}>{note.text}</InlineNote>}
              <details>
                <summary className="text-[11px] text-ink-faint cursor-pointer select-none hover:text-ink-dim">
                  自己的数据分别往哪加？
                </summary>
                <div className="mt-1 space-y-0.5 text-[11.5px] text-ink-dim leading-5">
                  <div>· 报文 → <code className="kbd-mono">tcms/tcms.dbc</code></div>
                  <div>· 故障 → <code className="kbd-mono">tcms/faults.yaml</code>（加条目 + 处置）</div>
                  <div>· 场景 → <code className="kbd-mono">scenarios/</code>（放一个 yaml）</div>
                </div>
              </details>
            </div>

            <div className="flex justify-end">
              <button className="btn" onClick={() => setStep(STEP.LLM)}>
                下一步：接入 AI →
              </button>
            </div>
          </div>
        )}

        {/* 第 1 步：LLM / API */}
        {step === STEP.LLM && (
          <div className="space-y-3">
            <Callout
              tone="dim"
              icon={<IconSpark className="h-4 w-4" />}
              title="这一步是可选的："
              details={
                <div className="space-y-1">
                  <div>· 不填 key：Agent 用离线规则决策，全流程照样跑通，零成本、不卡壳。</div>
                  <div>· 填了 key：规划 / 选场景 / 意图解析交给真实大模型，输出会标注这次实际用的模型名。</div>
                  <div>· 兼容任意 OpenAI 协议端点：阿里云百炼 / DeepSeek / OpenAI / 自建 vLLM。</div>
                  <div>· key 只写入本机设置文件，请求只发给你填的端点；仓库与日志里不含 key。</div>
                </div>
              }
            >
              不做任何配置就能完整体验；接上自己的模型，Agent 的决策才由真实大模型做出。
            </Callout>

            {presetNote && <InlineNote tone="info">{presetNote}</InlineNote>}

            <div className="grid sm:grid-cols-2 gap-2">
              <label className="text-[11px] text-ink-faint">
                服务商（选预设会自动填端点与推荐模型）
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
              <label className="text-[11px] text-ink-faint">
                API Key（留空 = 不修改已保存的 key）
                <input
                  className="input mt-1 font-mono"
                  type={showKey ? "text" : "password"}
                  placeholder={st?.llm.has_key ? "已保存（留空保持不变）" : "sk-…"}
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                />
              </label>
              <label className="text-[11px] text-ink-faint">
                Base URL
                <input
                  className="input mt-1 font-mono text-[12px]"
                  placeholder="https://…/v1"
                  value={baseUrl}
                  onChange={(e) => {
                    setBaseUrl(e.target.value);
                    setModels([]);
                    setProbeState("idle");
                    setPresetNote("");
                  }}
                />
              </label>
              <div className="text-[11px] text-ink-faint">
                模型
                {manualMode ? (
                  <input
                    className="input mt-1 font-mono"
                    placeholder="deepseek-v3.2 / deepseek-chat / …"
                    value={model}
                    onChange={(e) => {
                      setModel(e.target.value);
                      setPresetNote("");
                    }}
                  />
                ) : (
                  <>
                    <select
                      className="select w-full mt-1"
                      value={model}
                      onChange={(e) => {
                        setModel(e.target.value);
                        setPresetNote("");
                      }}
                      disabled={models.length === 0 && probeState !== "done"}
                    >
                      {models.length === 0 ? (
                        <option value="">（点下方「测试连接并获取模型」拉取真实清单，或手动输入）</option>
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
                        {probeState === "loading" ? (
                          "连接中…"
                        ) : probeState === "done" ? (
                          <>
                            <IconReplay className="h-4 w-4" />
                            重新获取模型
                          </>
                        ) : (
                          <>
                            <IconBolt className="h-4 w-4" />
                            测试连接并获取模型
                          </>
                        )}
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
              <button className="btn btn-sm" onClick={() => void saveLlm()} disabled={saving}>
                保存 AI 配置
              </button>
              <label className="flex items-center gap-1.5 text-[11px] text-ink-faint cursor-pointer">
                <input type="checkbox" checked={showKey} onChange={(e) => setShowKey(e.target.checked)} /> 显示 key
              </label>
              {st?.llm.has_key && (
                <button className="btn-ghost btn-sm" onClick={() => void clearKey()} disabled={saving}>
                  清除已存 key
                </button>
              )}
              <span className="ml-auto text-[10.5px] text-ink-faint">
                {st?.llm.has_key ? "本机已保存 key；输入框留空表示不动它" : "本机尚未保存 key"}
              </span>
            </div>
            {note?.at === "llm" && <InlineNote tone={note.tone}>{note.text}</InlineNote>}

            <div className="flex justify-between">
              <button className="btn-ghost" onClick={() => setStep(STEP.ASSETS)}>
                ← 上一步
              </button>
              <button className="btn" onClick={() => setStep(STEP.ENGINE)}>
                下一步：检查引擎 →
              </button>
            </div>
          </div>
        )}

        {/* 第 2 步：引擎检查 */}
        {step === STEP.ENGINE && (
          <div className="space-y-3">
            <div className="text-[13px] leading-6">
              场景真实执行 / Agent 需要 <b className="text-ink">TCMS 引擎</b>（tcms-can-test）。
            </div>
            <div className="grid gap-1.5 sm:grid-cols-2 rounded-[var(--radius-md)] border border-line-soft bg-surface-2/40 p-3">
              <div className="flex items-center gap-2 text-[12.5px]">
                <StatusDot tone={!statusKnown ? "warn" : engineOk ? "ok" : "warn"} pulse={statusKnown && !engineOk} />
                TCMS 引擎：
                {!statusKnown ? "状态未知" : engineOk ? `可用（v${sys?.engine.version ?? ""}）` : "不可用"}
              </div>
              <div className="flex items-center gap-2 text-[12.5px]">
                <StatusDot tone={!statusKnown ? "warn" : llmReady ? "ok" : "info"} />
                AI 后端：
                {!statusKnown ? "状态未知" : llmReady ? "LLM（已配 key）" : "Mock（离线，可完整演示）"}
              </div>
            </div>
            {!statusKnown && (
              <InlineNote tone="warn">
                状态接口这次没答上来，引擎与 AI 后端状态未知；资产浏览与知识图谱不依赖引擎，可以先照常使用。
              </InlineNote>
            )}
            {statusKnown && !engineOk && (
              <Callout
                tone="warn"
                icon={<IconWarn className="h-4 w-4" />}
                title="引擎不可用不影响先体验："
                details={
                  <div className="space-y-1">
                    <div>· 在终端执行：pip install -e ".[upstream]"（安装 tcms-can-test 引擎）。</div>
                    <div>· 或者把第①步的资产目录指向已安装 tcms-can-test 的路径，然后重启服务。</div>
                  </div>
                }
              >
                资产浏览、知识图谱、故障演示都还能用；只有场景执行与 Agent 会提示引导。
              </Callout>
            )}
            <div className="flex justify-between">
              <button className="btn-ghost" onClick={() => setStep(STEP.LLM)}>
                ← 上一步
              </button>
              <button className="btn" onClick={() => setStep(STEP.DONE)}>
                下一步：完成 →
              </button>
            </div>
          </div>
        )}

        {/* 第 3 步：完成 */}
        {step === STEP.DONE && (
          <div className="space-y-3">
            <div className="text-[13px] leading-6">都设置好了。接下来你可以（点卡片直达）：</div>
            <div className="grid sm:grid-cols-2 xl:grid-cols-3 gap-2 text-[12px]">
              {nextSteps.map((n) => {
                const inner = (
                  <>
                    <div className="flex items-center gap-1.5 font-medium text-ink">{n.icon}{n.t}</div>
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
                    onClick={() => setStep(n.back ?? STEP.ASSETS)}
                    className="panel bg-surface-2/40 px-3 py-2.5 text-left w-full hover:border-info/40 transition-colors cursor-pointer"
                  >
                    {inner}
                  </button>
                );
              })}
            </div>
            <div className="flex justify-between items-center">
              <button className="btn-ghost" onClick={() => setStep(STEP.ENGINE)}>
                ← 上一步
              </button>
              {!st?.onboarding_done ? (
                <button className="btn" onClick={() => void finishOnboarding()} disabled={saving}>
                  我完成了，开始使用 →
                </button>
              ) : (
                <Tag tone="ok">引导已完成</Tag>
              )}
            </div>
            {note?.at === "onboarding" && <InlineNote tone={note.tone}>{note.text}</InlineNote>}
          </div>
        )}
      </Panel>

      {/* ============ 进阶参考（不是第二条向导：这里只是"还能从外部改什么"的索引） ============
          原来它叫「扩展与集成」，用三张并列卡片把资产源 / AI / 环境变量又讲了一遍，
          和上面的向导抢同一批事；现在压成一张参考表 + 两张只读表，视觉权重降到向导之下。 */}
      <Panel
        dense
        className="bg-surface-2/30"
        title="进阶：外部可配置接口"
        sub="不 fork、不改平台代码"
        right={<Tag tone="dim">给开发者 / 现场部署</Tag>}
      >
        <Callout
          tone="dim"
          icon={<IconGear className="h-4 w-4" />}
          title="三类扩展点都开放成外部接口："
          details={
            <div className="space-y-1">
              <div>· 取参优先级：环境变量 &gt; 设置文件 &gt; 内置默认。</div>
              <KV k="设置文件" v={st?.dir ?? "~/.tcms-ai-platform/settings.json"} mono />
              <div>· 设置文件不进仓库；改设置文件一般需重启服务，环境变量请在启动前设置。</div>
            </div>
          }
        >
          内容（资产 / 故障 / 场景）、模型（LLM 端点）与运行参数，都能从外部改。
        </Callout>

        {/* 三行参考表：怎么配 + 当前取值 + 入口（取代原来三张并列大卡） */}
        <div className="table-scroll mt-3">
          <table style={{ minWidth: 760 }}>
            <thead>
              <tr>
                <th className="th">扩展点</th>
                <th className="th">怎么配</th>
                <th className="th">当前取值 / 状态</th>
                <th className="th">入口</th>
              </tr>
            </thead>
            <tbody>
              <tr className="tr-hover">
                <td className="td whitespace-nowrap">
                  <span className="text-ink font-medium">数据扩展</span>{" "}
                  <code className="kbd-mono text-[11px]">assets</code>
                </td>
                <td className="td text-ink-dim text-[12.5px]">
                  往你的 tcms-can-test 目录加内容，重启后自动加载：报文 <code className="kbd-mono">tcms/tcms.dbc</code>、
                  故障 <code className="kbd-mono">tcms/faults.yaml</code>、场景 <code className="kbd-mono">scenarios/</code>
                </td>
                <td className="td">
                  <span className="inline-flex items-center gap-1.5 text-[12px]">
                    <StatusDot tone={!statusKnown ? "warn" : isBundled ? "info" : "ok"} />
                    <span className={!statusKnown ? "text-warn" : isBundled ? "text-ink-dim" : "text-ok"}>
                      {srcFriendly}
                    </span>
                  </span>
                </td>
                <td className="td">
                  <Link to="/assets" className="text-info text-xs hover:underline whitespace-nowrap">
                    去资产页 →
                  </Link>
                </td>
              </tr>
              <tr className="tr-hover">
                <td className="td whitespace-nowrap">
                  <span className="text-ink font-medium">AI / API 扩展</span>{" "}
                  <code className="kbd-mono text-[11px]">llm</code>
                </td>
                <td className="td text-ink-dim text-[12.5px]">
                  换成任意 OpenAI 兼容端点：<code className="kbd-mono">base_url</code> +{" "}
                  <code className="kbd-mono">model</code> + <code className="kbd-mono">key</code>
                  （阿里云百炼 / DeepSeek / OpenAI / 自建 v1）
                </td>
                <td className="td">
                  <span className="inline-flex items-center gap-1.5 text-[12px]">
                    <StatusDot tone={!statusKnown ? "warn" : llmReady ? "ok" : "info"} />
                    {!statusKnown ? "状态未知" : llmReady ? "已配 key" : "未配 key（离线 Mock）"}
                  </span>
                  <div
                    className="kbd-mono text-[11px] text-ink-faint truncate"
                    title={`${provLabel || "未选择服务商"} · ${st?.llm.model?.trim() || "默认 deepseek-v3.2"}`}
                  >
                    {provLabel || "未选择服务商"} · {st?.llm.model?.trim() || "默认 deepseek-v3.2"}
                  </div>
                </td>
                <td className="td">
                  <button className="btn-ghost btn-sm whitespace-nowrap" onClick={() => setStep(STEP.LLM)}>
                    去第②步填 key →
                  </button>
                </td>
              </tr>
              <tr className="tr-hover">
                <td className="td whitespace-nowrap">
                  <span className="text-ink font-medium">环境变量扩展</span>{" "}
                  <code className="kbd-mono text-[11px]">env</code>
                </td>
                <td className="td text-ink-dim text-[12.5px]">
                  启动服务前设置，优先级最高，适合部署 / 现场：{" "}
                  <code className="kbd-mono">TCMS_UPSTREAM_DIR</code> · <code className="kbd-mono">TCMS_AI_HOME</code> ·{" "}
                  <code className="kbd-mono">PORT</code> · <code className="kbd-mono">LLM_BASE_URL</code> ·{" "}
                  <code className="kbd-mono">LLM_MODEL</code> · <code className="kbd-mono">DASH_API_KEY</code>
                </td>
                <td className="td text-[12px] text-ink-dim">取值见下表（只读）</td>
                <td className="td text-[11px] text-ink-faint whitespace-nowrap">无需改动平台代码</td>
              </tr>
            </tbody>
          </table>
        </div>

        {/* 能力矩阵：机器自证「当前开哪些能力」 */}
        <div className="mt-4 pt-3 border-t border-line-soft">
          <div className="flex items-center justify-between mb-2">
            <span className="section-title">能力矩阵 · 当前开哪些能力</span>
            {statusKnown ? (
              <Tag tone="ok">机器自证</Tag>
            ) : (
              <Tag tone="warn">状态接口不可用 · 下表按默认口径</Tag>
            )}
          </div>
          <div className="table-scroll">
            <table style={{ minWidth: 760 }}>
              <thead>
                <tr>
                  <th className="th">能力</th>
                  <th className="th">名称</th>
                  <th className="th">说明</th>
                  <th className="th">依赖</th>
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
                        <Tag tone="dim">{CAP_DEP[r.kind]}</Tag>
                      </td>
                      <td className="td">
                        <span className="inline-flex items-center gap-1.5 text-[12px]">
                          <StatusDot tone={meta.tone} />
                          <span
                            className={
                              meta.tone === "ok" ? "text-ok" : meta.tone === "warn" ? "text-warn" : "text-ink-dim"
                            }
                          >
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

        {/* 环境变量键值表（只读） */}
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
                      <span className="inline-flex items-center gap-2 text-[12px] min-w-0">
                        <StatusDot tone={r.tone} />
                        <span
                          className={`num truncate ${
                            r.tone === "warn" ? "text-warn" : r.tone === "ok" ? "text-ok" : "text-ink-dim"
                          }`}
                          title={r.val}
                        >
                          {r.val}
                        </span>
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-[11px] text-ink-faint mt-2">API key 永不回显：只显示「已设置 / 未设置」。</p>
        </div>
      </Panel>

      {!st && !err && (
        <Panel bodyClass="p-3">
          <EmptyState compact icon={<IconGear className="h-4 w-4" />} title="正在读取本机设置…" desc="读取设置文件与引擎状态。" />
        </Panel>
      )}
    </div>
  );
}
