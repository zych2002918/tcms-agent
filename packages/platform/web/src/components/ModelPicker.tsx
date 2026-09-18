/**
 * 模型选择器：**手动获取模型列表 → 选一个 → 只作用于本次运行**。
 *
 * 设计取舍（用户视角）：
 * 1. **不藏在设置页**。用户是在"要跑一次"的时候才想起换模型的，
 *    所以选择器长在运行入口旁边，不用先跳走再跳回来；
 * 2. **默认是"跟随设置"**，并把它显示成人话（"跟随设置（deepseek-v4-pro-0813）"）——
 *    用户得先知道现在用的是什么，才谈得上换；
 * 3. **列表要能筛**。兼容端点（如百炼）会返回上百个模型，混着 embedding / 语音 /
 *    视觉模型；直接平铺等于把选择成本丢给用户。这里按名称粗分"非对话类"并折叠，
 *    同时给搜索框——分组只是减少噪音，不替用户下结论；
 * 4. **失败要能自救**。拉列表失败（端点不支持 GET /models、key 没配、网络不通）
 *    时不做成一个死掉的空列表，而是给出中文原因 + 保留"手动输入模型名"这条路。
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { type ModelChoice, isOverridden } from "../lib/modelChoice";

/** 按名称粗分"非对话类"模型（embedding / 语音 / 视觉 / 重排…）。
 *  这是**启发式**，只用来减少噪音：拿不准的一律留在"对话"里，不武断隐藏。 */
const NON_CHAT = /embed|rerank|text-vec|ocr|tts|asr|speech|audio|image|video|wanx|vl-|voice/i;

type ModelRow = { id: string; owned_by?: string; created?: number };

export function ModelPicker({
  value,
  onChange,
  followLabel,
  keyConfigured,
  disabled,
}: {
  value: ModelChoice;
  onChange: (c: ModelChoice) => void;
  /** 设置里的默认模型（用于把"跟随设置"说清楚） */
  followLabel: string;
  /** 是否已配置 API key：未配置时如实说明会走离线规则臂 */
  keyConfigured: boolean;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [models, setModels] = useState<ModelRow[]>([]);
  const [probe, setProbe] = useState<"idle" | "loading" | "done" | "error">("idle");
  const [probeErr, setProbeErr] = useState("");
  const [q, setQ] = useState("");
  const [manual, setManual] = useState("");
  const [showAll, setShowAll] = useState(false);
  /** 连接自检结果：回答"我现在选的这个模型真的能用吗"（不是"配没配 key"） */
  const [pingState, setPingState] = useState<"idle" | "loading">("idle");
  const [pingRes, setPingRes] = useState<Awaited<ReturnType<typeof api.llmPing>> | null>(null);
  const boxRef = useRef<HTMLDivElement | null>(null);

  // 点外面 / Esc 关闭（键盘可关，不只是鼠标）
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  /** 拉一次真实模型列表（带 key 的探测；失败给出可照做的原因） */
  const fetchModels = async () => {
    setProbe("loading");
    setProbeErr("");
    try {
      const r = await api.llmModels();
      if (!r.ok) {
        setProbe("error");
        setProbeErr(r.error || "获取失败");
        return;
      }
      setModels(r.models);
      setProbe("done");
    } catch (e) {
      setProbe("error");
      setProbeErr(e instanceof Error ? e.message : String(e));
    }
  };

  const { chat, other } = useMemo(() => {    const kw = q.trim().toLowerCase();
    const hit = (m: ModelRow) => !kw || m.id.toLowerCase().includes(kw);
    const list = models.filter(hit);
    return {
      chat: list.filter((m) => !NON_CHAT.test(m.id)),
      other: list.filter((m) => NON_CHAT.test(m.id)),
    };
  }, [models, q]);

  const label = value.mode === "pick" ? value.id : `跟随设置（${followLabel}）`;

  /** 连接自检：真发一次最小请求。服务端把**原样错误**回给我们，不美化。 */
  const ping = async () => {
    setPingState("loading");
    setPingRes(null);
    try {
      const body =
        value.mode === "pick"
          ? { model: value.id, ...(value.base_url ? { base_url: value.base_url } : {}) }
          : {};
      setPingRes(await api.llmPing(body));
    } catch (e) {
      setPingRes({
        ok: false,
        model: value.mode === "pick" ? value.id : followLabel,
        base_url: "",
        latency_ms: null,
        error: e instanceof Error ? e.message : String(e),
        note: "",
      });
    } finally {
      setPingState("idle");
    }
  };

  const pick = (c: ModelChoice) => {
    onChange(c);
    setOpen(false);
  };

  return (
    <div className="relative" ref={boxRef}>
      <button
        type="button"
        className="btn-ghost max-w-[320px] disabled:opacity-50"
        onClick={() => setOpen((v) => !v)}
        disabled={disabled}
        aria-expanded={open}
        aria-haspopup="dialog"
        title={
          value.mode === "pick"
            ? `本次运行使用 ${value.id}（不改设置）`
            : `跟随设置：${followLabel}`
        }
      >
        <span
          className={`h-1.5 w-1.5 rounded-full shrink-0 ${
            keyConfigured ? (isOverridden(value) ? "bg-vio" : "bg-info") : "bg-ink-faint"
          }`}
        />
        <span className="truncate kbd-mono">{label}</span>
        <span className="text-ink-faint shrink-0">▾</span>
      </button>

      {open && (
        <div
          className="panel-float absolute right-0 z-[var(--z-popover)] mt-1.5 w-[400px] max-w-[92vw] p-0 overflow-hidden step-in"
          role="dialog"
          aria-label="选择本次运行使用的模型"
        >
          <div className="px-3 pt-2.5 pb-2 flex items-start gap-2">
            <div className="min-w-0 flex-1">
              <div className="text-[12px] font-semibold text-ink">本次运行使用哪个模型</div>
              <div className="text-[11px] text-ink-faint mt-0.5 leading-4">
                只影响这一次运行，<b className="text-ink-dim font-medium">不会</b>修改设置里的默认模型。
              </div>
            </div>
            <button
              type="button"
              className="btn-soft shrink-0"
              onClick={() => void ping()}
              disabled={pingState === "loading"}
              title="真发一次最小请求，验证我现在选的这个模型能不能用（配了 key ≠ 能用）"
            >
              {pingState === "loading" ? "自检中…" : "测试连接"}
            </button>
          </div>

          {pingRes && (
            <div
              className={`mx-3 mb-2 rounded-[var(--radius-md)] border px-2.5 py-2 text-[11px] leading-4 ${
                pingRes.ok ? "border-ok/35 bg-ok/10 text-ok" : "border-bad/35 bg-bad/10 text-bad"
              }`}
            >
              {pingRes.ok ? (
                <>
                  ✓ 可用：<span className="kbd-mono">{pingRes.model}</span>
                  {pingRes.latency_ms != null ? ` · ${pingRes.latency_ms}ms` : ""}
                </>
              ) : (
                <>
                  ✗ 用不了：<span className="kbd-mono">{pingRes.model}</span>
                  <div className="mt-0.5 text-ink-dim break-all">{pingRes.error}</div>
                </>
              )}
              {pingRes.note ? <div className="mt-0.5 text-ink-faint">注：{pingRes.note}</div> : null}
            </div>
          )}

          {/* 跟随设置 */}
          <button
            type="button"
            onClick={() => pick({ mode: "follow" })}
            className={`w-full text-left px-3 py-2 hover:bg-surface-2 transition-colors border-t border-line-soft ${
              value.mode === "follow" ? "bg-info/5" : ""
            }`}
          >
            <div className="flex items-center gap-2">
              <span className={`text-[12px] ${value.mode === "follow" ? "text-info" : "text-ink"}`}>
                {value.mode === "follow" ? "●" : "○"} 跟随设置
              </span>
              <span className="kbd-mono text-[11px] text-ink-dim truncate">{followLabel}</span>
            </div>
            <div className="text-[11px] text-ink-faint mt-0.5">
              在「设置 / 引导」里改过的默认模型；换机器、换 key 都跟着走。
            </div>
          </button>

          {/* 模型列表 */}
          <div className="border-t border-line-soft">
            <div className="flex items-center gap-2 px-3 py-2">
              <span className="text-[11px] text-ink-dim shrink-0">可用模型</span>
              {probe === "done" && models.length > 0 && (
                <input
                  className="input py-1 text-[12px] flex-1"
                  placeholder="筛选（如 qwen / deepseek / turbo）"
                  value={q}
                  onChange={(e) => setQ(e.target.value)}
                  aria-label="筛选模型"
                />
              )}
              <button
                type="button"
                className="btn-soft shrink-0 ml-auto"
                onClick={() => void fetchModels()}
                disabled={probe === "loading"}
                title="带 key 请求一次 {base_url}/models，拿到你这个账号真实可用的清单"
              >
                {probe === "loading" ? "获取中…" : probe === "done" ? "↻ 重新获取" : "⚡ 获取模型列表"}
              </button>
            </div>

            {!keyConfigured && (
              <div className="px-3 pb-2 text-[11px] text-warn leading-4">
                还没配 API key：现在拉不到列表，运行也会走<b className="text-ink-dim font-medium">离线规则臂</b>（不是 LLM 决策）。
                去「设置 / 引导」填 key 后再来。
              </div>
            )}
            {probe === "error" && (
              <div className="px-3 pb-2 text-[11px] text-warn leading-4">
                获取失败：{probeErr}
                <br />
                多为端点不支持 <code className="kbd-mono">GET /models</code> 或 key 无权限——
                下面手动输入模型名照样能跑。
              </div>
            )}

            {probe === "done" && (
              <div className="max-h-[248px] overflow-y-auto border-t border-line-soft">
                {chat.length === 0 && (
                  <div className="px-3 py-3 text-[11px] text-ink-faint">
                    没有匹配「{q}」的对话类模型。
                  </div>
                )}
                {chat.map((m) => (
                  <ModelRowItem
                    key={m.id}
                    m={m}
                    active={value.mode === "pick" && value.id === m.id}
                    onPick={() => pick({ mode: "pick", id: m.id })}
                  />
                ))}
                {other.length > 0 && (
                  <>
                    <button
                      type="button"
                      onClick={() => setShowAll((v) => !v)}
                      className="w-full text-left px-3 py-1.5 text-[11px] text-ink-faint hover:text-ink-dim hover:bg-surface-2 transition-colors border-t border-line-soft"
                    >
                      {showAll ? "▾" : "▸"} 另有 {other.length} 个非对话类模型
                      （嵌入 / 重排 / 语音 / 视觉，一般不用来决策）
                    </button>
                    {showAll &&
                      other.map((m) => (
                        <ModelRowItem
                          key={m.id}
                          m={m}
                          dim
                          active={value.mode === "pick" && value.id === m.id}
                          onPick={() => pick({ mode: "pick", id: m.id })}
                        />
                      ))}
                  </>
                )}
              </div>
            )}
          </div>

          {/* 手动输入 */}
          <div className="border-t border-line-soft px-3 py-2">
            <div className="text-[11px] text-ink-dim mb-1">或手动输入模型名</div>
            <div className="flex gap-2">
              <input
                className="input py-1 text-[12px] flex-1"
                placeholder="如 qwen-turbo / deepseek-v3.2 / glm-4-flash"
                value={manual}
                onChange={(e) => setManual(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && manual.trim()) pick({ mode: "pick", id: manual.trim() });
                }}
                aria-label="手动输入模型名"
              />
              <button
                type="button"
                className="btn-soft shrink-0"
                disabled={!manual.trim()}
                onClick={() => pick({ mode: "pick", id: manual.trim() })}
              >
                使用
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function ModelRowItem({
  m,
  active,
  dim,
  onPick,
}: {
  m: ModelRow;
  active: boolean;
  dim?: boolean;
  onPick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onPick}
      className={`w-full text-left px-3 py-1.5 hover:bg-surface-2 transition-colors flex items-center gap-2 ${
        active ? "bg-info/5" : ""
      }`}
    >
      <span className={`text-[11px] shrink-0 ${active ? "text-info" : "text-ink-faint"}`}>
        {active ? "●" : "○"}
      </span>
      <span className={`kbd-mono text-[12px] truncate ${dim ? "text-ink-faint" : "text-ink"}`}>
        {m.id}
      </span>
      {m.owned_by && (
        <span className="text-[10px] text-ink-faint ml-auto shrink-0 truncate max-w-[110px]">
          {m.owned_by}
        </span>
      )}
    </button>
  );
}
