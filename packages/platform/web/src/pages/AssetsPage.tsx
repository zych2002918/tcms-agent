import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  api,
  type FaultInfo,
  type FunctionInfo,
  type MessageInfo,
  type RequirementRow,
  type ScenarioInfo,
  type SignalInfo,
} from "../api";
import { Panel, Tag, SkeletonRows, Explain, EmptyState } from "../components/ui";

type Tab = "messages" | "signals" | "faults" | "requirements" | "scenarios" | "functions";

const LEVEL_TONE: Record<string, "ok" | "warn" | "bad" | "info"> = {
  info: "info",
  minor: "info",
  major: "warn",
  critical: "bad",
};

/** 场景步骤精简视图（时间线迷你预览用；来自只读 GET /api/scenarios/{file}，不依赖 api.ts 扩展） */
type ScenarioStepLite = { at: number; action: string; fault: string | null };
const STEP_LABEL: Record<string, string> = { inject: "注入", recover: "恢复" };

async function fetchScenarioSteps(file: string): Promise<ScenarioStepLite[]> {
  try {
    const r = await fetch(`/api/scenarios/${encodeURIComponent(file)}`);
    if (!r.ok) return [];
    const d = (await r.json()) as { steps?: unknown[] };
    if (!Array.isArray(d.steps)) return [];
    return d.steps
      .map((st) => {
        const s = st as { at?: unknown; action?: unknown; fault?: unknown };
        return {
          at: Number(s.at),
          action: String(s.action ?? ""),
          fault: s.fault == null ? null : String(s.fault),
        };
      })
      .filter((st) => Number.isFinite(st.at) && (st.action === "inject" || st.action === "recover"));
  } catch {
    return [];
  }
}

/** 合法 tab 值（URL query 校验；未知回默认 messages） */
const TAB_IDS: Tab[] = ["messages", "signals", "faults", "requirements", "scenarios", "functions"];

export function AssetsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  // tab 初值来自 URL ?tab=...；此后受控于用户点击 + 同步回 URL
  const rawTab = searchParams.get("tab");
  const [tab, setTab] = useState<Tab>(() => (TAB_IDS.includes(rawTab as Tab) ? (rawTab as Tab) : "messages"));
  const [messages, setMessages] = useState<MessageInfo[]>([]);
  const [signals, setSignals] = useState<SignalInfo[]>([]);
  const [faults, setFaults] = useState<FaultInfo[]>([]);
  const [reqs, setReqs] = useState<RequirementRow[]>([]);
  const [scenarios, setScenarios] = useState<ScenarioInfo[]>([]);
  const [functions, setFunctions] = useState<FunctionInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [selFault, setSelFault] = useState<FaultInfo | null>(null);
  const [q, setQ] = useState("");
  const [focusId, setFocusId] = useState<string | null>(null);
  const rowRefs = useRef<Record<string, HTMLTableRowElement | null>>({});
  // 场景资产库：按需懒加载每个场景的步骤细节（时间线迷你预览数据源）
  const [stepLite, setStepLite] = useState<Record<string, ScenarioStepLite[]>>({});

  // 一次拉全六类资产（场景/功能也一并加载，tab 切换即时）
  useEffect(() => {
    setLoading(true);
    Promise.all([api.messages(), api.signals(), api.faults(), api.requirements(), api.scenarios(), api.functions()])
      .then(([m, s, f, r, sc, fn]) => {
        setMessages(m);
        setSignals(s);
        setFaults(f);
        setReqs(r);
        setScenarios(sc);
        setFunctions(fn);
      })
      .catch(() => undefined)
      .finally(() => setLoading(false));
  }, []);

  // URL 直达：?tab=&focus= → 设置 tab + 打开/高亮目标行
  useEffect(() => {
    const t = searchParams.get("tab");
    if (t && TAB_IDS.includes(t as Tab)) setTab(t as Tab);
    const focus = searchParams.get("focus");
    if (focus) {
      setFocusId(focus);
      // 清掉一次性 focus（replace，不留历史噪声）
      const next = new URLSearchParams(searchParams);
      next.delete("focus");
      setSearchParams(next, { replace: true });
    }
  }, [searchParams, setSearchParams]);

  // tab 切换同步回 URL（replace，不产生后退噪声）
  const selectTab = useCallback(
    (t: Tab) => {
      setTab(t);
      const next = new URLSearchParams(searchParams);
      next.set("tab", t);
      setSearchParams(next, { replace: true });
    },
    [searchParams, setSearchParams]
  );

  // 目标行出现后滚动 + 短暂高亮
  useEffect(() => {
    if (!focusId) return;
    const el = rowRefs.current[focusId];
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "center" });
      const t = setTimeout(() => setFocusId(null), 2600);
      return () => clearTimeout(t);
    }
  }, [focusId, tab, loading]);

  // focus 若是故障 key → 打开对应详情侧栏（等 faults 就绪后匹配）
  useEffect(() => {
    if (!focusId || !faults.length) return;
    const f = faults.find((x) => x.key === focusId);
    if (f) setSelFault(f);
  }, [focusId, faults]);

  const tabs: { id: Tab; label: string; n: number; what: string }[] = [
    { id: "messages", label: "报文", n: messages.length, what: "设备间互发的 CAN 消息" },
    { id: "signals", label: "信号", n: signals.length, what: "报文里的数值/状态" },
    { id: "faults", label: "故障", n: faults.length, what: "可注入的异常及其处置" },
    { id: "scenarios", label: "场景", n: scenarios.length, what: "可真实执行的故障场景" },
    { id: "requirements", label: "安全需求", n: reqs.length, what: "必须满足的安全要求" },
    { id: "functions", label: "被测功能", n: functions.length, what: "列车视角的功能聚合" },
  ];

  const kw = q.trim().toLowerCase();

  // 统一关键词过滤
  const filter = (arr: unknown[], fields: string[]) =>
    !kw
      ? arr
      : arr.filter((row) => fields.some((f) => String((row as Record<string, unknown>)[f] ?? "").toLowerCase().includes(kw)));

  const filteredMessages = useMemo(() => filter(messages, ["name", "node", "send_type"]) as MessageInfo[], [messages, kw]);
  const filteredSignals = useMemo(() => filter(signals, ["name", "message", "unit"]) as SignalInfo[], [signals, kw]);
  const filteredFaults = useMemo(() => filter(faults, ["fid", "key", "name", "subsystem", "action"]) as FaultInfo[], [faults, kw]);
  const filteredScenarios = useMemo(() => filter(scenarios, ["file", "name"]) as ScenarioInfo[], [scenarios, kw]);
  const filteredFunctions = useMemo(() => filter(functions, ["fid", "name", "description"]) as FunctionInfo[], [functions, kw]);

  // 场景资产库：场景 tab 可见时，为当前行懒加载步骤细节（19 条也不卡：只对当前筛出行 fetch）
  useEffect(() => {
    if (tab !== "scenarios" || loading) return;
    const missing = filteredScenarios.filter((s) => !(s.file in stepLite));
    if (missing.length === 0) return;
    let alive = true;
    missing.forEach((s) => {
      fetchScenarioSteps(s.file).then((steps) => {
        if (alive) setStepLite((m) => (m[s.file] ? m : { ...m, [s.file]: steps }));
      });
    });
    return () => {
      alive = false;
    };
  }, [tab, loading, filteredScenarios, stepLite]);

  const focusCls = (id: string) =>
    focusId === id ? "!bg-info/10 transition-colors duration-700" : "";

  return (
    <div className="mx-auto w-full max-w-[1840px] space-y-4">
      {/* 类型切换 = 左对齐 tab 组 */}
      <div className="flex items-center gap-1 border-b border-line-soft overflow-x-auto pb-0">
        {tabs.map((t) => (
          <button
            key={t.id}
            onClick={() => selectTab(t.id)}
            className={`px-3.5 py-2 text-[13px] whitespace-nowrap border-b-2 transition-colors ${
              tab === t.id ? "border-info text-ink font-medium" : "border-transparent text-ink-dim hover:text-ink"
            }`}
            title={t.what}
          >
            {t.label}
            <span className="ml-1 text-[11px] text-ink-faint num">{t.n}</span>
          </button>
        ))}
        <div className="ml-auto w-56 min-w-40 pb-1">
          <input className="input !py-1.5 text-[12px]" placeholder="筛选… 如 overspeed" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
      </div>

      {loading ? (
        <Panel>
          <SkeletonRows rows={6} cols={4} />
        </Panel>
      ) : (
        <>
          {/* 报文 */}
          {tab === "messages" && (
            <Panel
              title="报文"
              right={<Tag tone="dim">DBC 协议库</Tag>}
              bodyClass="p-0"
            >
              <Explain text="报文 = 车上设备之间定时互发的消息。点一行可看它含哪些信号（在图谱中定位）。" />
              <div className="table-scroll mt-1">
                <table>
                  <thead>
                    <tr>
                      <th className="th">报文</th>
                      <th className="th">ID</th>
                      <th className="th">发送方</th>
                      <th className="th">周期</th>
                      <th className="th">信号数</th>
                      <th className="th">动作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredMessages.map((m) => (
                      <tr
                        key={m.name}
                        ref={(el) => {
                          rowRefs.current[m.name] = el;
                        }}
                        className={`tr-hover ${focusCls(m.name)}`}
                      >
                        <td className="td font-medium text-ink">{m.name}</td>
                        <td className="td kbd-mono">{m.frame_id}</td>
                        <td className="td">
                          <Tag tone="dim">{m.node}</Tag>
                        </td>
                        <td className="td num">{m.cycle_ms ? `${m.cycle_ms} ms` : <Tag tone="warn">事件</Tag>}</td>
                        <td className="td num">{m.signals.length}</td>
                        <td className="td">
                          <button className="btn-ghost btn-sm" title={`在图谱中查看 ${m.name} 的关联`} onClick={() => (window.location.href = `/graph?focus=${m.name}`)}>
                            图谱 →
                          </button>
                        </td>
                      </tr>
                    ))}
                    {filteredMessages.length === 0 && (
                      <tr>
                        <td colSpan={6}>
                          <EmptyState icon="?" title="无匹配报文" desc="换个关键词试试" />
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </Panel>
          )}

          {/* 信号 */}
          {tab === "signals" && (
            <Panel title="信号" right={<Tag tone="dim">{signals.length} 个</Tag>} bodyClass="p-0">
              <Explain text="信号 = 报文里携带的单个数值/状态。枚举型信号会列出它的取值含义（如 0=关、1=开、2=故障）。" />
              <div className="table-scroll mt-1">
                <table>
                  <thead>
                    <tr>
                      <th className="th">信号</th>
                      <th className="th">所属报文</th>
                      <th className="th">单位</th>
                      <th className="th">取值含义</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredSignals.map((s) => (
                      <tr
                        key={s.name}
                        ref={(el) => {
                          rowRefs.current[s.name] = el;
                        }}
                        className={`tr-hover ${focusCls(s.name)}`}
                      >
                        <td className="td font-medium text-ink">{s.name}</td>
                        <td className="td kbd-mono">{s.message}</td>
                        <td className="td">{s.unit || "—"}</td>
                        <td className="td">
                          {s.choices.length > 0 ? (
                            <div className="flex flex-wrap gap-1">
                              {s.choices.map((c) => (
                                <Tag key={c.value} tone="vio">
                                  {c.value}={c.label}
                                </Tag>
                              ))}
                            </div>
                          ) : (
                            <span className="text-ink-faint text-xs">数值型</span>
                          )}
                        </td>
                      </tr>
                    ))}
                    {filteredSignals.length === 0 && (
                      <tr>
                        <td colSpan={4}>
                          <EmptyState icon="?" title="无匹配信号" desc="换个关键词试试" />
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </Panel>
          )}

          {/* 故障 */}
          {tab === "faults" && (
            <div className="grid lg:grid-cols-5 gap-4 items-start">
              <div className="lg:col-span-3">
                <Panel title="故障字典（FMEA）" right={<Tag tone="dim">{faults.length} 条 · 全部可注入</Tag>} bodyClass="p-0">
                  <Explain text="故障 = 可注入的异常。每条都规定了：什么等级（信号灯颜色）、系统该做什么处置。点一行看细节。" />
                  <div className="table-scroll mt-1">
                    <table>
                      <thead>
                        <tr>
                          <th className="th">故障</th>
                          <th className="th">子系统</th>
                          <th className="th">等级</th>
                          <th className="th">处置</th>
                        </tr>
                      </thead>
                      <tbody>
                        {filteredFaults.map((f) => (
                          <tr
                            key={f.key}
                            ref={(el) => {
                              rowRefs.current[f.key] = el;
                            }}
                            className={`tr-hover cursor-pointer ${selFault?.key === f.key ? "!bg-info/5" : ""} ${focusCls(f.key)}`}
                            onClick={() => setSelFault(f)}
                          >
                            <td className="td">
                              <div className="font-medium text-ink">{f.name}</div>
                              <div className="kbd-mono text-[11px]">
                                {f.fid} · {f.key}
                              </div>
                            </td>
                            <td className="td">
                              <Tag tone="dim">{f.subsystem}</Tag>
                            </td>
                            <td className="td">
                              <Tag tone={LEVEL_TONE[f.level] ?? "info"}>{f.level}</Tag>
                            </td>
                            <td className="td kbd-mono">{f.action}</td>
                          </tr>
                        ))}
                        {filteredFaults.length === 0 && (
                          <tr>
                            <td colSpan={4}>
                              <EmptyState icon="?" title="无匹配故障" desc="换个关键词试试" />
                            </td>
                          </tr>
                        )}
                      </tbody>
                    </table>
                  </div>
                </Panel>
              </div>
              {/* 详情侧栏 */}
              <div className="lg:col-span-2">
                {selFault ? (
                  <Panel
                    title={
                      <>
                        <Tag tone={LEVEL_TONE[selFault.level] ?? "info"}>{selFault.level}</Tag> {selFault.name}
                      </>
                    }
                    right={
                      <button className="btn-ghost btn-sm" onClick={() => setSelFault(null)}>
                        ✕
                      </button>
                    }
                  >
                    <div className="kbd-mono text-[11px] mb-2">
                      {selFault.fid} · {selFault.key}
                    </div>
                    <dl className="space-y-2 text-[13px]">
                      <div>
                        <dt className="text-ink-faint text-[11px]">描述</dt>
                        <dd className="text-ink leading-5">{selFault.desc}</dd>
                      </div>
                      <div className="grid grid-cols-2 gap-2">
                        <div>
                          <dt className="text-ink-faint text-[11px]">子系统</dt>
                          <dd>{selFault.subsystem}</dd>
                        </div>
                        <div>
                          <dt className="text-ink-faint text-[11px]">安全等级 SIL</dt>
                          <dd>
                            <Tag tone={Number(selFault.sil) >= 3 ? "bad" : Number(selFault.sil) >= 2 ? "warn" : "dim"}>
                              {selFault.sil}
                            </Tag>
                          </dd>
                        </div>
                        <div>
                          <dt className="text-ink-faint text-[11px]">处置动作</dt>
                          <dd className="kbd-mono">{selFault.action}</dd>
                          {selFault.action_note && (
                            <dd className="mt-1 text-[11.5px] text-vio leading-5 border-t border-line-soft pt-1">
                              ⚙ 处置取决于原因：{selFault.action_note}
                            </dd>
                          )}
                        </div>
                        <div>
                          <dt className="text-ink-faint text-[11px]">注入层</dt>
                          <dd>{selFault.layer}</dd>
                        </div>
                      </div>
                      <div>
                        <dt className="text-ink-faint text-[11px]">如何检测</dt>
                        <dd className="leading-5">{selFault.detect}</dd>
                      </div>
                      <div>
                        <dt className="text-ink-faint text-[11px]">如何注入</dt>
                        <dd className="leading-5">{selFault.inject}</dd>
                      </div>
                      <div>
                        <dt className="text-ink-faint text-[11px]">如何恢复</dt>
                        <dd className="leading-5">{selFault.recovery}</dd>
                      </div>
                    </dl>
                    <div className="mt-3 flex gap-2">
                      <button className="btn btn-sm" onClick={() => (window.location.href = `/graph?focus=fault:${selFault.key}`)}>
                        在图谱中查看 →
                      </button>
                    </div>
                  </Panel>
                ) : (
                  <Panel>
                    <EmptyState icon="☝" title="点左侧任一故障" desc="这里会显示它的等级、处置、检测与恢复方式。" />
                  </Panel>
                )}
              </div>
            </div>
          )}

          {/* 场景（资产库视图：每场景含故障等级 chip + 步骤时间线预览 + 执行/看动画入口） */}
          {tab === "scenarios" && (
            <Panel
              title={
                <>
                  场景资产库
                  <span className="ml-1 text-xs font-normal text-ink-faint num">（{scenarios.length} 个 · 来自资产源 scenarios/）</span>
                </>
              }
              right={<Tag tone="dim">可真实执行</Tag>}
              bodyClass="p-0"
            >
              <Explain text="场景 = 一份按时间编排的故障注入/恢复剧本（YAML）。每行展示注入哪些故障（等级色 chip）、几步编排、时间线前段预览；「执行」去场景执行页真实运行，「看动画」跳 FaultLab 把该场景资产变成动画。" />
              <div className="table-scroll mt-1">
                <table>
                  <thead>
                    <tr>
                      <th className="th">场景</th>
                      <th className="th">注入故障</th>
                      <th className="th">编排步数</th>
                      <th className="th">时间线预览</th>
                      <th className="th">涉及节点</th>
                      <th className="th">动作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredScenarios.map((s) => {
                      const steps = stepLite[s.file] ?? [];
                      const preview = steps.slice(0, 3).map((st) => `${st.at}s ${STEP_LABEL[st.action] ?? st.action}${st.fault ? " " + st.fault : ""}`);
                      return (
                        <tr
                          key={s.file}
                          ref={(el) => {
                            rowRefs.current[s.file] = el;
                          }}
                          className={`tr-hover ${focusCls(s.file)}`}
                        >
                          <td className="td">
                            <div className="font-medium text-ink">{s.name}</div>
                            <div className="kbd-mono text-[11px]">{s.file}</div>
                          </td>
                          <td className="td">
                            <div className="flex flex-wrap gap-1">
                              {s.fault_keys.map((fk) => {
                                const f = faults.find((x) => x.key === fk);
                                return (
                                  <Tag key={fk} tone={f ? LEVEL_TONE[f.level] ?? "warn" : "warn"} title={f ? `${f.name} · ${f.level}` : undefined}>
                                    {fk}
                                  </Tag>
                                );
                              })}
                            </div>
                          </td>
                          <td className="td num">含 {s.steps} 步</td>
                          <td className="td">
                            {steps.length > 0 ? (
                              <div className="flex flex-col gap-0.5 max-w-[300px]">
                                {preview.map((p, i) => (
                                  <span key={i} className="text-[11px] text-ink-dim truncate mono">· {p}</span>
                                ))}
                                {steps.length > preview.length && (
                                  <span className="text-[10px] text-ink-faint num">…共 {steps.length} 步</span>
                                )}
                              </div>
                            ) : (
                              <span className="text-[11px] text-ink-faint">{s.steps} 步编排（加载中…）</span>
                            )}
                          </td>
                          <td className="td">
                            <div className="flex flex-wrap gap-1">
                              {s.nodes.map((n) => (
                                <Tag key={n} tone="dim">
                                  {n}
                                </Tag>
                              ))}
                            </div>
                          </td>
                          <td className="td">
                            <div className="flex flex-col gap-1 items-start">
                              <a
                                className="btn-ghost btn-sm shrink-0"
                                href="/scenarios"
                                title={`在场景执行页真实运行 ${s.file}`}
                                onClick={(e) => {
                                  e.preventDefault();
                                  window.location.href = `/scenarios?file=${encodeURIComponent(s.file)}`;
                                }}
                              >
                                执行 →
                              </a>
                              <a
                                className="btn-ghost btn-sm shrink-0"
                                href={`/faultlab?scenario=${encodeURIComponent(s.file)}`}
                                title={`跳 FaultLab 演示 ${s.file} 的动画`}
                              >
                                ▶ 看动画
                              </a>
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                    {filteredScenarios.length === 0 && (
                      <tr>
                        <td colSpan={6}>
                          <EmptyState icon="?" title="无匹配场景" desc="换个关键词试试" />
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </Panel>
          )}

          {/* 需求 */}
          {tab === "requirements" && (
            <Panel title="需求追溯矩阵 (RTM)" right={<Tag tone="dim">SR-01 ~ SR-18</Tag>} bodyClass="p-0">
              <Explain text="每条安全需求都被实现模块与测试用例覆盖。这是“我测的东西有依据”的证明。" />
              <div className="table-scroll mt-1">
                <table>
                  <thead>
                    <tr>
                      <th className="th">需求</th>
                      <th className="th">实现模块</th>
                      <th className="th">验证用例</th>
                      <th className="th">覆盖的行为</th>
                    </tr>
                  </thead>
                  <tbody>
                    {reqs.flatMap((r) =>
                      r.rows.map((row, i) => (
                        <tr
                          key={`${r.req_id}-${i}`}
                          ref={(el) => {
                            rowRefs.current[r.req_id] = el;
                          }}
                          className={`tr-hover ${focusCls(r.req_id)}`}
                        >
                          <td className="td">
                            <code className="kbd-mono">{r.req_id}</code>
                          </td>
                          <td className="td kbd-mono">{row.module}</td>
                          <td className="td kbd-mono">{row.test_file}</td>
                          <td className="td text-ink-dim">{row.verifies}</td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </Panel>
          )}

          {/* 被测功能（列车视角聚合：功能 = 报文 + 信号 + 故障 + 需求） */}
          {tab === "functions" && (
            <Panel title="被测功能（列车视角的测试对象）" right={<Tag tone="dim">{functions.length} 个 · F-EBM / F-ATP / F-DOOR / F-NET</Tag>} bodyClass="p-0">
              <Explain text="被测功能把“测报文”升维为“测功能”：每个功能聚合它关联的报文、信号、故障与安全需求——测试对象的列车视角。" />
              <div className="table-scroll mt-1">
                <table>
                  <thead>
                    <tr>
                      <th className="th">功能</th>
                      <th className="th">一句话</th>
                      <th className="th">关联报文 / 信号</th>
                      <th className="th">关联故障</th>
                      <th className="th">覆盖需求</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredFunctions.map((fn) => (
                      <tr
                        key={fn.fid}
                        ref={(el) => {
                          rowRefs.current[fn.fid] = el;
                        }}
                        className={`tr-hover ${focusCls(fn.fid)}`}
                      >
                        <td className="td">
                          <code className="kbd-mono">{fn.fid}</code>
                          <div className="font-medium text-ink text-[13px] mt-0.5">{fn.name}</div>
                        </td>
                        <td className="td text-ink-dim">{fn.description}</td>
                        <td className="td">
                          <div className="flex flex-wrap gap-1">
                            {fn.messages.map((mm) => (
                              <Tag key={mm} tone="dim">
                                {mm}
                              </Tag>
                            ))}
                            {fn.signals.slice(0, 6).map((sg) => (
                              <Tag key={sg} tone="vio">
                                {sg}
                              </Tag>
                            ))}
                          </div>
                        </td>
                        <td className="td">
                          <div className="flex flex-wrap gap-1">
                            {fn.fault_keys.map((fk) => (
                              <Tag key={fk} tone="warn">
                                {fk}
                              </Tag>
                            ))}
                          </div>
                        </td>
                        <td className="td">
                          <div className="flex flex-wrap gap-1">
                            {fn.requirements.map((rq) => (
                              <Tag key={rq} tone="ok">
                                {rq}
                              </Tag>
                            ))}
                          </div>
                        </td>
                      </tr>
                    ))}
                    {filteredFunctions.length === 0 && (
                      <tr>
                        <td colSpan={5}>
                          <EmptyState icon="?" title="无匹配功能" desc="换个关键词试试" />
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </Panel>
          )}
        </>
      )}
    </div>
  );
}
