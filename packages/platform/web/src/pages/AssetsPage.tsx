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
import { Callout, EmptyState, Explain, KV, Panel, SkeletonRows, Tabs, Tag } from "../components/ui";

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

/* ---------- 表格呈现约定（这一页是"表格密集"页，规范集中在这里，避免每张表各写一套） ----------
 * 1. 表头 sticky：滚动时不丢列名。层级取全局刻度 --z-sticky，不随手写 z-10；
 *    背景必须不透明（bg-surface），否则行的内容会从表头下面透出来。
 * 2. 数字列：表头与单元格同时右对齐 + tabular-nums，刷新时数字不跳动。
 * 3. 长文本列：truncate + title 全文，行高不被长路径撑爆。
 * 4. 滚动容器：横向可滚（窄屏不错位）+ 纵向封顶——只有当它是滚动容器时，表头才真的会"粘住"。
 * 5. 一次最多渲染 PAGE 行：信号 116 条、故障 203 条，全量铺出来既慢又难读。
 * ------------------------------------------------------------------ */
const PAGE = 50;
const TH = "th sticky top-0 z-[var(--z-sticky)] bg-surface";
const THR = `${TH} text-right`;
const TABLE_WRAP = "table-scroll max-h-[min(70vh,720px)] overflow-y-auto border-t border-line-soft";

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

/** 表尾：告诉用户"看到的是全部还是前一段"，并把"显示更多"放在看得见的位置。 */
function TableFoot({ shown, total, onMore }: { shown: number; total: number; onMore: () => void }) {
  if (total === 0) return null;
  return (
    <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1.5 border-t border-line-soft px-3 py-2">
      <span className="text-[11px] text-ink-faint">
        已显示 <span className="num text-ink-dim">{shown}</span> / <span className="num text-ink-dim">{total}</span> 条
      </span>
      {total > shown && (
        <button className="btn-ghost btn-sm shrink-0" onClick={onMore}>
          显示更多 {Math.min(PAGE, total - shown)} 条
        </button>
      )}
    </div>
  );
}

/** 表内空态：分成两种说法——"筛没了"和"这一类本来就是空的"。
 *  混成一句会让用户以为数据坏了（实际只是关键词没命中）。 */
function EmptyRow({ colSpan, kw, onClear }: { colSpan: number; kw: string; onClear: () => void }) {
  return (
    <tr>
      <td colSpan={colSpan} className="px-3 py-3">
        <EmptyState
          compact
          icon="⌕"
          title={kw ? "没有匹配项" : "这一类资产是空的"}
          desc={
            kw
              ? `当前分类里没有包含「${kw}」的条目 —— 试试清空筛选，或换个字段值（如 overspeed、door、VCAM）。`
              : "后端没有返回这一类资产。确认设置里的资产源路径是否正确，以及 TCMS 引擎是否已安装。"
          }
          action={kw ? <button className="btn-ghost btn-sm" onClick={onClear}>清空筛选</button> : undefined}
        />
      </td>
    </tr>
  );
}

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
  const [loadErr, setLoadErr] = useState(false);
  const [selFault, setSelFault] = useState<FaultInfo | null>(null);
  /** 选中故障的详情（含列表接口不具备的散文字段）；未回来时用列表字段顶替 */
  const [faultDetail, setFaultDetail] = useState<FaultInfo | null>(null);
  const [q, setQ] = useState("");
  const [focusId, setFocusId] = useState<string | null>(null);
  const [limit, setLimit] = useState(PAGE); // 当前分类一次渲染多少行
  const rowRefs = useRef<Record<string, HTMLTableRowElement | null>>({});
  // 场景资产库：按需懒加载每个场景的步骤细节（时间线迷你预览数据源）
  const [stepLite, setStepLite] = useState<Record<string, ScenarioStepLite[]>>({});

  /* ---------- 派生数据（全部先于副作用声明：effect 的依赖数组在 render 期求值） ---------- */

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

  // 需求是"一条需求多行覆盖"的嵌套结构：先摊平成行，才能和别的表一样筛选 + 分页
  const reqRows = useMemo(
    () => reqs.flatMap((r) => r.rows.map((row, i) => ({ key: `${r.req_id}-${i}`, req_id: r.req_id, row }))),
    [reqs]
  );
  const filteredReqs = useMemo(() => {
    if (!kw) return reqRows;
    return reqRows.filter((r) =>
      [r.req_id, r.row.module, r.row.test_file, r.row.verifies].some((s) => s.toLowerCase().includes(kw))
    );
  }, [reqRows, kw]);

  /** 当前分类"筛选后"的条目 id：既用于统计（共 N 条 / 已筛出 M 条），也用于深链 focus 的行定位 */
  const ids = useMemo<string[]>(() => {
    switch (tab) {
      case "messages":
        return filteredMessages.map((m) => m.name);
      case "signals":
        return filteredSignals.map((s) => s.name);
      case "faults":
        return filteredFaults.map((f) => f.key);
      case "scenarios":
        return filteredScenarios.map((s) => s.file);
      case "functions":
        return filteredFunctions.map((f) => f.fid);
      case "requirements":
        return filteredReqs.map((r) => r.key);
      default:
        return [];
    }
  }, [tab, filteredMessages, filteredSignals, filteredFaults, filteredScenarios, filteredFunctions, filteredReqs]);

  const tabs: { id: Tab; label: string; n: number; what: string }[] = [
    { id: "messages", label: "报文", n: messages.length, what: "设备间互发的 CAN 消息" },
    { id: "signals", label: "信号", n: signals.length, what: "报文里的数值/状态" },
    { id: "faults", label: "故障", n: faults.length, what: "可注入的异常及其处置" },
    { id: "scenarios", label: "场景", n: scenarios.length, what: "可真实执行的故障场景" },
    { id: "requirements", label: "安全需求", n: reqs.length, what: "必须满足的安全要求" },
    { id: "functions", label: "被测功能", n: functions.length, what: "列车视角的功能聚合" },
  ];
  const activeTab = tabs.find((t) => t.id === tab) ?? tabs[0];
  /** "共 N 条"里的 N 与表格的渲染单位一致：需求表按"覆盖行"计（一条需求可能有多行覆盖），
   *  否则会出现"共 18 条 / 已筛出 31 条"这种自相矛盾的读数。 */
  const totalRows = tab === "requirements" ? reqRows.length : activeTab.n;

  /* ---------- 副作用 ---------- */

  // 一次拉全六类资产（场景/功能也一并加载，tab 切换即时）
  useEffect(() => {
    setLoading(true);
    setLoadErr(false);
    Promise.all([api.messages(), api.signals(), api.faults(), api.requirements(), api.scenarios(), api.functions()])
      .then(([m, s, f, r, sc, fn]) => {
        setMessages(m);
        setSignals(s);
        setFaults(f);
        setReqs(r);
        setScenarios(sc);
        setFunctions(fn);
      })
      .catch(() => setLoadErr(true))
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
  }, [focusId, tab, loading, limit]);

  // 深链目标若落在"显示更多"之外：先把那一段并进渲染上限，否则滚动与高亮都找不到节点。
  // 要写回 state（而不是只在渲染时临时放大）：否则 focus 高亮 2.6s 后自动清空，
  // 目标行会跟着消失——用户刚被带过去，行却没了。
  useEffect(() => {
    if (!focusId) return;
    const idx = ids.indexOf(focusId);
    if (idx >= 0 && idx >= limit) setLimit(Math.ceil((idx + 1) / PAGE) * PAGE);
  }, [focusId, ids, limit]);

  // focus 若是故障 key → 打开对应详情侧栏（等 faults 就绪后匹配）
  useEffect(() => {
    if (!focusId || !faults.length) return;
    const f = faults.find((x) => x.key === focusId);
    if (f) setSelFault(f);
  }, [focusId, faults]);

  /** 选中故障后拉一次详情：**列表接口不带散文字段**。
   *
   * `GET /api/faults` 只返回 fid/key/name/子系统/等级/处置/SIL 这些标量，
   * 而字典里真正教人干活的 desc / 如何检测 / 如何注入 / 如何恢复在
   * `GET /api/faults/{key}` 里。此前详情侧栏直接读列表对象，于是那四段
   * **永远渲染不出来**（代码看着对、界面一直是空的）——203 条故障的
   * "怎么检测、怎么注入、怎么恢复"全被吞了。
   * 现在选中即拉详情；拉取失败时退回列表字段（只显示拿得到的，不编造）。 */
  useEffect(() => {
    if (!selFault) {
      setFaultDetail(null);
      return;
    }
    let alive = true;
    const key = selFault.key;
    setFaultDetail(null); // 换故障先清空：避免把上一个故障的"如何恢复"显示给这一条
    api
      .fault(key)
      .then((d) => {
        if (alive) setFaultDetail(d);
      })
      .catch(() => undefined); // 失败静默退回：上面已有列表字段可用
    return () => {
      alive = false;
    };
  }, [selFault]);

  // 换分类或改关键词 → 回到第一段（否则会停在上一次"显示更多"的位置）
  useEffect(() => {
    setLimit(PAGE);
  }, [tab, kw]);

  // 场景资产库：场景 tab 可见时，为当前行懒加载步骤细节（只对当前筛出行 fetch）
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

  const focusCls = (id: string) => (focusId === id ? "!bg-info/10 transition-colors duration-700" : "");

  const clearQ = () => setQ("");
  const more = () => setLimit((n) => n + PAGE);
  /** 每张表统一的表尾："显示更多"的步长与统计口径只在这里改一次 */
  const foot = (shownTotal: number) => <TableFoot shown={Math.min(limit, shownTotal)} total={shownTotal} onMore={more} />;

  /** 详情侧栏的数据源：详情已到位就用详情（含散文字段），否则用列表行顶替。
   *  加 key 相等判断，避免快速切换故障时把上一条的详情显示给下一条。 */
  const fd = selFault && faultDetail?.key === selFault.key ? faultDetail : selFault;

  return (
    <div className="mx-auto w-full max-w-[1800px] space-y-4">
      {/* 顶部：分类切换 + 筛选。筛选是这一页的主操作，所以放在第一屏第一行的标题栏里，
          并回报"共 N 条 / 已筛出 M 条"——用户得先知道筛掉了多少，才知道要不要改条件。 */}
      <Panel
        title="测试资产库"
        sub={activeTab.what}
        right={
          <div className="flex items-center gap-2">
            <input
              className="input !py-1 w-48 text-[12px] lg:w-64"
              placeholder={`在「${activeTab.label}」里筛选…`}
              value={q}
              onChange={(e) => setQ(e.target.value)}
              aria-label="在当前分类中筛选资产"
            />
            {kw && (
              <button className="btn-ghost btn-sm shrink-0" onClick={clearQ} title="清空筛选关键词">
                清空
              </button>
            )}
          </div>
        }
        bodyClass="p-3"
      >
        <Tabs
          items={tabs.map((t) => ({ value: t.id, label: t.label, count: loading ? undefined : t.n, hint: t.what }))}
          value={tab}
          onChange={selectTab}
          className="max-w-full overflow-x-auto"
        />
        <div className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-ink-faint">
          <span>
            共 <span className="num text-ink-dim">{totalRows}</span> 条
          </span>
          {kw && (
            <>
              <span aria-live="polite">
                已筛出 <span className="num text-ink-dim">{ids.length}</span> 条
              </span>
              <span className="min-w-0 max-w-[260px] truncate" title={q.trim()}>
                关键词「{q.trim()}」
              </span>
            </>
          )}
        </div>
      </Panel>

      {loadErr ? (
        <Callout
          tone="warn"
          icon="⚠"
          title="资产没有读出来"
          details={
            <>
              <p>这一页一次读取六个只读接口：/api/messages、/api/signals、/api/faults、/api/requirements、/api/scenarios、/api/functions。</p>
              <p>任一接口失败都会让整批为空，因此只显示这条提示，而不是几张"看起来是空资产"的表。</p>
            </>
          }
        >
          后端没有返回资产清单。确认服务已启动后刷新页面重试——这里不显示空表，避免把"读不到"误读成"资产是空的"。
        </Callout>
      ) : loading ? (
        <Panel title="正在读取资产…" sub="报文 / 信号 / 故障 / 需求 / 场景 / 功能" bodyClass="p-3">
          <SkeletonRows rows={8} cols={5} />
        </Panel>
      ) : (
        <>
          {/* 报文 */}
          {tab === "messages" && (
            <Panel title="报文" sub="设备间定时互发的 CAN 消息" right={<Tag tone="dim">DBC 协议库</Tag>} bodyClass="p-0">
              <div className="px-3 pt-2.5 pb-2">
                <Explain text="报文 = 车上设备之间定时互发的消息。点一行可看它含哪些信号（在图谱中定位）。" />
              </div>
              <div className={TABLE_WRAP}>
                <table>
                  <thead>
                    <tr>
                      <th className={TH}>报文</th>
                      <th className={TH}>ID</th>
                      <th className={TH}>发送方</th>
                      <th className={THR}>周期</th>
                      <th className={THR}>信号数</th>
                      <th className={TH}>动作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredMessages.slice(0, limit).map((m) => (
                      <tr
                        key={m.name}
                        ref={(el) => {
                          rowRefs.current[m.name] = el;
                        }}
                        className={`tr-hover ${focusCls(m.name)}`}
                      >
                        <td className="td">
                          <div className="max-w-[240px] truncate font-medium text-ink" title={m.name}>
                            {m.name}
                          </div>
                        </td>
                        <td className="td kbd-mono whitespace-nowrap">{m.frame_id}</td>
                        <td className="td">
                          <Tag tone="dim">{m.node}</Tag>
                        </td>
                        <td className="td num whitespace-nowrap text-right">
                          {m.cycle_ms ? `${m.cycle_ms} ms` : <Tag tone="warn">事件</Tag>}
                        </td>
                        <td
                          className="td num text-right"
                          title={m.signals.length ? `信号：${m.signals.join("、")}` : "这条报文没有信号"}
                        >
                          {m.signals.length}
                        </td>
                        <td className="td">
                          <button
                            className="btn-ghost btn-sm whitespace-nowrap"
                            title={`在图谱中查看 ${m.name} 的关联`}
                            onClick={() => (window.location.href = `/graph?focus=${m.name}`)}
                          >
                            图谱 →
                          </button>
                        </td>
                      </tr>
                    ))}
                    {filteredMessages.length === 0 && <EmptyRow colSpan={6} kw={kw} onClear={clearQ} />}
                  </tbody>
                </table>
              </div>
              {foot(filteredMessages.length)}
            </Panel>
          )}

          {/* 信号 */}
          {tab === "signals" && (
            <Panel title="信号" sub="报文里携带的单个数值 / 状态" right={<Tag tone="dim">{signals.length} 个</Tag>} bodyClass="p-0">
              <div className="px-3 pt-2.5 pb-2">
                <Explain text="信号 = 报文里携带的单个数值/状态。枚举型信号会列出它的取值含义（如 0=关、1=开、2=故障）。" />
              </div>
              <div className={TABLE_WRAP}>
                <table>
                  <thead>
                    <tr>
                      <th className={TH}>信号</th>
                      <th className={TH}>所属报文</th>
                      <th className={TH}>单位</th>
                      <th className={TH}>取值含义</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredSignals.slice(0, limit).map((s) => (
                      <tr
                        key={s.name}
                        ref={(el) => {
                          rowRefs.current[s.name] = el;
                        }}
                        className={`tr-hover ${focusCls(s.name)}`}
                      >
                        <td className="td">
                          <div className="max-w-[240px] truncate font-medium text-ink" title={s.name}>
                            {s.name}
                          </div>
                        </td>
                        <td className="td kbd-mono">
                          <span className="block max-w-[220px] truncate" title={s.message}>
                            {s.message}
                          </span>
                        </td>
                        <td className="td">
                          <span className="block max-w-[120px] truncate" title={s.unit || "无量纲"}>
                            {s.unit || "—"}
                          </span>
                        </td>
                        <td className="td">
                          {s.choices.length > 0 ? (
                            <div className="flex max-w-[520px] flex-wrap gap-1">
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
                    {filteredSignals.length === 0 && <EmptyRow colSpan={4} kw={kw} onClear={clearQ} />}
                  </tbody>
                </table>
              </div>
              {foot(filteredSignals.length)}
            </Panel>
          )}

          {/* 故障 */}
          {tab === "faults" && (
            <div className="grid lg:grid-cols-5 gap-4 items-start">
              <div className="lg:col-span-3">
                <Panel
                  title="故障字典（FMEA）"
                  sub="每条都写明了等级与系统应做的处置"
                  right={<Tag tone="dim">{faults.length} 条 · 全部可注入</Tag>}
                  bodyClass="p-0"
                >
                  <div className="px-3 pt-2.5 pb-2">
                    <Explain text="故障 = 可注入的异常。每条都规定了：什么等级（信号灯颜色）、系统该做什么处置。点一行看细节。" />
                  </div>
                  <div className={TABLE_WRAP}>
                    <table>
                      <thead>
                        <tr>
                          <th className={TH}>故障</th>
                          <th className={TH}>子系统</th>
                          <th className={TH}>等级</th>
                          <th className={TH}>处置</th>
                        </tr>
                      </thead>
                      <tbody>
                        {filteredFaults.slice(0, limit).map((f) => (
                          <tr
                            key={f.key}
                            ref={(el) => {
                              rowRefs.current[f.key] = el;
                            }}
                            className={`tr-hover cursor-pointer ${selFault?.key === f.key ? "!bg-info/5" : ""} ${focusCls(f.key)}`}
                            onClick={() => setSelFault(f)}
                          >
                            <td className="td">
                              <div className="max-w-[240px] truncate font-medium text-ink" title={`${f.name}（${f.fid} · ${f.key}）`}>
                                {f.name}
                              </div>
                              <div className="kbd-mono text-[11px] truncate" title={`${f.fid} · ${f.key}`}>
                                {f.fid} · {f.key}
                              </div>
                            </td>
                            <td className="td">
                              <Tag tone="dim">{f.subsystem}</Tag>
                            </td>
                            <td className="td">
                              <Tag tone={LEVEL_TONE[f.level] ?? "info"}>{f.level}</Tag>
                            </td>
                            <td className="td kbd-mono">
                              <span className="block max-w-[180px] truncate" title={f.action}>
                                {f.action}
                              </span>
                            </td>
                          </tr>
                        ))}
                        {filteredFaults.length === 0 && <EmptyRow colSpan={4} kw={kw} onClear={clearQ} />}
                      </tbody>
                    </table>
                  </div>
                  {foot(filteredFaults.length)}
                </Panel>
              </div>
              {/* 详情侧栏：标量字段用键值行排整齐（原来是一堆 dt/dd 竖排，字段名与值对不齐） */}
              <div className="lg:col-span-2">
                {fd ? (
                  <Panel
                    title={
                      <>
                        <Tag tone={LEVEL_TONE[fd.level] ?? "info"}>{fd.level}</Tag> {fd.name}
                      </>
                    }
                    right={
                      <button className="btn-ghost btn-sm" onClick={() => setSelFault(null)} title="关闭详情">
                        ✕
                      </button>
                    }
                    bodyClass="p-3"
                  >
                    <div className="space-y-1">
                      <KV k="故障 ID" v={fd.fid} mono />
                      <KV k="故障 key" v={fd.key} mono />
                      <KV k="子系统" v={fd.subsystem} />
                      <KV
                        k="安全等级 SIL"
                        v={
                          <Tag tone={Number(fd.sil) >= 3 ? "bad" : Number(fd.sil) >= 2 ? "warn" : "dim"}>
                            {fd.sil}
                          </Tag>
                        }
                      />
                      <KV k="处置动作" v={fd.action} mono />
                      <KV k="注入层" v={fd.layer} />
                    </div>
                    {fd.action_note && (
                      <p className="mt-2.5 rounded-[var(--radius-md)] border border-vio/30 bg-vio/8 px-2.5 py-1.5 text-[11.5px] leading-5 text-vio">
                        ⚙ 处置取决于原因：{fd.action_note}
                      </p>
                    )}
                    {/* 散文类字段用 KV wrap：与上面的标量字段同一套排版，
                        但整段读得完（截断一段说明等于没写） */}
                    <div className="mt-3 space-y-2">
                      {[
                        { k: "描述", v: fd.desc },
                        { k: "如何检测", v: fd.detect },
                        { k: "如何注入", v: fd.inject },
                        { k: "如何恢复", v: fd.recovery },
                      ]
                        .filter((x) => x.v)
                        .map((x) => (
                          <KV key={x.k} k={x.k} v={x.v} wrap />
                        ))}
                    </div>
                    <div className="mt-3">
                      <button
                        className="btn btn-sm"
                        onClick={() => (window.location.href = `/graph?focus=fault:${fd.key}`)}
                      >
                        在图谱中查看 →
                      </button>
                    </div>
                  </Panel>
                ) : (
                  <Panel bodyClass="p-3">
                    <EmptyState
                      compact
                      icon="☝"
                      title="还没选故障"
                      desc="点左侧任一故障，这里会显示它的等级、处置动作，以及如何检测 / 注入 / 恢复。"
                    />
                  </Panel>
                )}
              </div>
            </div>
          )}

          {/* 场景（资产库视图：每场景含故障等级 chip + 步骤时间线预览 + 执行/看动画入口） */}
          {tab === "scenarios" && (
            <Panel
              title="场景资产库"
              sub="来自资产源 scenarios/ · 每个都可真实执行"
              right={<Tag tone="dim">{scenarios.length} 个</Tag>}
              bodyClass="p-0"
            >
              <div className="px-3 pt-2.5 pb-2">
                <Explain text="场景 = 一份按时间编排的故障注入/恢复剧本（YAML）。每行给出注入哪些故障、几步编排与时间线预览；「执行」去场景执行页真实运行，「看动画」跳 FaultLab。" />
              </div>
              <div className={TABLE_WRAP}>
                <table>
                  <thead>
                    <tr>
                      <th className={TH}>场景</th>
                      <th className={TH}>注入故障</th>
                      <th className={THR}>编排步数</th>
                      <th className={TH}>时间线预览</th>
                      <th className={TH}>涉及节点</th>
                      <th className={TH}>动作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredScenarios.slice(0, limit).map((s) => {
                      const steps = stepLite[s.file] ?? [];
                      return (
                        <tr
                          key={s.file}
                          ref={(el) => {
                            rowRefs.current[s.file] = el;
                          }}
                          className={`tr-hover ${focusCls(s.file)}`}
                        >
                          <td className="td">
                            <div className="max-w-[240px] truncate font-medium text-ink" title={`${s.name}（${s.file}）`}>
                              {s.name}
                            </div>
                            <div className="kbd-mono text-[11px] truncate" title={s.file}>
                              {s.file}
                            </div>
                          </td>
                          <td className="td">
                            <div className="flex max-w-[260px] flex-wrap gap-1">
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
                          <td className="td num whitespace-nowrap text-right">含 {s.steps} 步</td>
                          <td className="td">
                            {steps.length > 0 ? (
                              <div className="max-w-[320px] space-y-0.5">
                                {steps.slice(0, 3).map((st, i) => (
                                  <KV key={i} k={`${st.at}s`} v={`${STEP_LABEL[st.action] ?? st.action}${st.fault ? " " + st.fault : ""}`} mono />
                                ))}
                                {steps.length > 3 && (
                                  <div className="text-[10px] text-ink-faint num">…共 {steps.length} 步</div>
                                )}
                              </div>
                            ) : (
                              <span className="text-[11px] text-ink-faint num">{s.steps} 步编排（加载中…）</span>
                            )}
                          </td>
                          <td className="td">
                            <div className="flex max-w-[220px] flex-wrap gap-1">
                              {s.nodes.map((n) => (
                                <Tag key={n} tone="dim">
                                  {n}
                                </Tag>
                              ))}
                            </div>
                          </td>
                          <td className="td">
                            <div className="flex flex-col items-start gap-1">
                              <a
                                className="btn-ghost btn-sm shrink-0 whitespace-nowrap"
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
                                className="btn-ghost btn-sm shrink-0 whitespace-nowrap"
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
                    {filteredScenarios.length === 0 && <EmptyRow colSpan={6} kw={kw} onClear={clearQ} />}
                  </tbody>
                </table>
              </div>
              {foot(filteredScenarios.length)}
            </Panel>
          )}

          {/* 需求 */}
          {tab === "requirements" && (
            <Panel
              title="需求追溯矩阵 (RTM)"
              sub="每条安全需求 → 实现模块 → 验证用例"
              right={<Tag tone="dim">SR-01 ~ SR-18</Tag>}
              bodyClass="p-0"
            >
              <div className="px-3 pt-2.5 pb-2">
                <Explain text="每条安全需求都被实现模块与测试用例覆盖。这是「我测的东西有依据」的证明。" />
              </div>
              <div className={TABLE_WRAP}>
                <table>
                  <thead>
                    <tr>
                      <th className={TH}>需求</th>
                      <th className={TH}>实现模块</th>
                      <th className={TH}>验证用例</th>
                      <th className={TH}>覆盖的行为</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredReqs.slice(0, limit).map((r) => (
                      <tr
                        key={r.key}
                        ref={(el) => {
                          rowRefs.current[r.req_id] = el;
                        }}
                        className={`tr-hover ${focusCls(r.req_id)}`}
                      >
                        <td className="td">
                          <code className="kbd-mono whitespace-nowrap">{r.req_id}</code>
                        </td>
                        <td className="td kbd-mono">
                          <span className="block max-w-[220px] truncate" title={r.row.module}>
                            {r.row.module}
                          </span>
                        </td>
                        <td className="td kbd-mono">
                          <span className="block max-w-[240px] truncate" title={r.row.test_file}>
                            {r.row.test_file}
                          </span>
                        </td>
                        <td className="td">
                          <span className="block max-w-[420px] truncate text-ink-dim" title={r.row.verifies}>
                            {r.row.verifies}
                          </span>
                        </td>
                      </tr>
                    ))}
                    {filteredReqs.length === 0 && <EmptyRow colSpan={4} kw={kw} onClear={clearQ} />}
                  </tbody>
                </table>
              </div>
              {foot(filteredReqs.length)}
            </Panel>
          )}

          {/* 被测功能（列车视角聚合：功能 = 报文 + 信号 + 故障 + 需求） */}
          {tab === "functions" && (
            <Panel
              title="被测功能（列车视角的测试对象）"
              sub="功能 = 报文 + 信号 + 故障 + 需求"
              right={<Tag tone="dim">{functions.length} 个 · F-EBM / F-ATP / F-DOOR / F-NET</Tag>}
              bodyClass="p-0"
            >
              <div className="px-3 pt-2.5 pb-2">
                <Explain text="被测功能把「测报文」升维为「测功能」：每个功能聚合它关联的报文、信号、故障与安全需求——测试对象的列车视角。" />
              </div>
              <div className={TABLE_WRAP}>
                <table>
                  <thead>
                    <tr>
                      <th className={TH}>功能</th>
                      <th className={TH}>一句话</th>
                      <th className={TH}>关联报文 / 信号</th>
                      <th className={TH}>关联故障</th>
                      <th className={TH}>覆盖需求</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredFunctions.slice(0, limit).map((fn) => (
                      <tr
                        key={fn.fid}
                        ref={(el) => {
                          rowRefs.current[fn.fid] = el;
                        }}
                        className={`tr-hover ${focusCls(fn.fid)}`}
                      >
                        <td className="td">
                          <code className="kbd-mono whitespace-nowrap">{fn.fid}</code>
                          <div className="mt-0.5 max-w-[200px] truncate text-[13px] font-medium text-ink" title={fn.name}>
                            {fn.name}
                          </div>
                        </td>
                        <td className="td">
                          <span className="block max-w-[320px] truncate text-ink-dim" title={fn.description}>
                            {fn.description}
                          </span>
                        </td>
                        <td className="td">
                          <div className="flex max-w-[300px] flex-wrap gap-1">
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
                            {fn.signals.length > 6 && (
                              <Tag tone="dim" title={fn.signals.join("、")}>
                                +{fn.signals.length - 6} 个信号
                              </Tag>
                            )}
                          </div>
                        </td>
                        <td className="td">
                          <div className="flex max-w-[220px] flex-wrap gap-1">
                            {fn.fault_keys.map((fk) => (
                              <Tag key={fk} tone="warn">
                                {fk}
                              </Tag>
                            ))}
                          </div>
                        </td>
                        <td className="td">
                          <div className="flex max-w-[220px] flex-wrap gap-1">
                            {fn.requirements.map((rq) => (
                              <Tag key={rq} tone="ok">
                                {rq}
                              </Tag>
                            ))}
                          </div>
                        </td>
                      </tr>
                    ))}
                    {filteredFunctions.length === 0 && <EmptyRow colSpan={5} kw={kw} onClear={clearQ} />}
                  </tbody>
                </table>
              </div>
              {foot(filteredFunctions.length)}
            </Panel>
          )}
        </>
      )}
    </div>
  );
}
