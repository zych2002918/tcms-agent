import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type Stats } from "../api";
import { Panel, Tag, StatusDot } from "../components/ui";

export function Dashboard() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [health, setHealth] = useState<{ status: string; version: string; engine_version: string | null } | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    api.stats().then(setStats).catch((e) => setErr(String(e)));
    api.health().then(setHealth).catch(() => undefined);
  }, []);

  // 从这里开始 —— 三个主行动（用户探索的锚点）
  const actions = [
    {
      to: "/faultlab",
      icon: "⚙",
      tone: "text-warn border-warn/30 bg-warn/10",
      title: "看故障如何发生",
      desc: "选一个真实故障场景，用动画看它如何被检测、系统如何处置——不是花哨动效，每个事件都可溯源。",
      cta: "去故障演示",
    },
    {
      to: "/scenarios",
      icon: "▶",
      tone: "text-info border-info/30 bg-info/10",
      title: "跑一个故障场景",
      desc: "选「车门故障级联」等真实场景，看 TCMS 引擎如何处置、断言是否通过。",
      cta: "去执行场景",
    },
    {
      to: "/graph",
      icon: "◈",
      tone: "text-vio border-vio/30 bg-vio/10",
      title: "问 TCMS 领域知识",
      desc: "输入「车门故障不能发车」这类问题，返回带证据链的答案，小白也能看懂。",
      cta: "去知识图谱",
    },
    {
      to: "/agent",
      icon: "✦",
      tone: "text-ok border-ok/30 bg-ok/10",
      title: "指挥 AI 测试 Agent",
      desc: "给 Agent 一个真实任务（如验证紧急制动处置），它自主检索、执行并汇报轨迹。",
      cta: "去 Agent 工作台",
    },
  ];

  return (
    <div className="mx-auto w-full max-w-[1840px] space-y-5">
      {err && (
        <div className="panel border-bad/40 bg-bad/10 px-4 py-2.5 text-sm text-bad">⚠ 无法连接后端：{err}</div>
      )}

      {/* 系统状态条 */}
      <Panel title="系统状态" bodyClass="py-3">
        <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-[13px]">
          <span className="flex items-center gap-2">
            <StatusDot tone={health?.status === "ok" ? "ok" : "bad"} pulse />
            <span>
              {health?.status === "ok" ? "平台运行中" : "平台异常"} · v
              {stats?.version ?? "–"}
              {health?.engine_version ? (
                <span className="ml-1 text-ink-faint">· 引擎 v{health.engine_version}</span>
              ) : (
                <span className="ml-1 text-ink-faint">· 引擎未接入</span>
              )}
            </span>
          </span>
          <span className="text-ink-dim">
            资产源：<span className="kbd-mono">{stats?.source_upstream ?? "…"}</span>
          </span>
          <span className="ml-auto text-ink-faint text-xs" title="所有数字由真实资产加载派生，机器自证">
            数字机器自证 ✓
          </span>
        </div>
      </Panel>

      {/* 资产速览（六卡可点 → 直达测试资产页对应 tab；hover 提示可点击） */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
        {[
          { to: "/assets?tab=messages", value: stats?.messages ?? "–", label: "报文 (DBC)", num: "text-info", hint: "DBC 协议库定义的报文数" },
          { to: "/assets?tab=signals", value: stats?.signals ?? "–", label: "信号", num: "text-vio", hint: "报文内含的信号数" },
          { to: "/assets?tab=faults", value: stats?.faults ?? "–", label: "故障 (FMEA)", num: "text-warn", hint: "可注入的故障字典条目" },
          { to: "/assets?tab=scenarios", value: stats?.scenarios ?? "–", label: "场景", num: "text-ok", hint: "可真实执行的故障场景" },
          { to: "/assets?tab=requirements", value: stats?.req_ids ?? "–", label: "安全需求", num: "text-info", hint: "RTM 追溯矩阵需求数" },
          { to: "/assets?tab=functions", value: stats?.functions ?? "–", label: "被测功能", num: "text-ok", hint: "列车视角功能聚合" },
        ].map((c) => (
          <Link
            key={c.label}
            to={c.to}
            title={`${c.hint} — 点击直达测试资产`}
            className="panel panel-hover group relative block px-4 py-3"
          >
            <span className="pointer-events-none absolute right-2.5 top-2 text-[11px] text-info/0 transition-colors group-hover:text-info/90" aria-hidden>
              →
            </span>
            <div className={`stat-num ${c.num}`}>{c.value}</div>
            <div className="text-xs text-ink-dim mt-0.5 flex items-center gap-1">
              {c.label}
              <span className="text-info/0 transition-all group-hover:text-info/80 group-hover:translate-x-0.5" aria-hidden>
                →
              </span>
            </div>
          </Link>
        ))}
      </div>

      {/* 从这里开始 —— 行动入口 */}
      <div>
        <div className="section-title mb-2 px-1">从这里开始</div>
        <div className="grid md:grid-cols-2 xl:grid-cols-4 gap-3">
          {actions.map((a) => (
            <Link key={a.to} to={a.to} className="panel panel-hover block p-4 group">
              <div className={`inline-flex h-9 w-9 items-center justify-center rounded-lg border text-lg ${a.tone}`}>
                {a.icon}
              </div>
              <div className="mt-3 font-medium text-ink text-[14px]">{a.title}</div>
              <div className="mt-1 text-xs text-ink-dim leading-5">{a.desc}</div>
              <div className="mt-3 text-xs text-info flex items-center gap-1 opacity-80 group-hover:opacity-100 group-hover:gap-2 transition-all">
                {a.cta} <span>→</span>
              </div>
            </Link>
          ))}
        </div>
      </div>

      {/* 资产健康度明细（原被测功能表下沉，带说明） */}
      <Panel
        title="被测功能（列车视角的测试对象）"
        right={<Tag tone="dim">F-EBM / F-ATP / F-DOOR / F-NET</Tag>}
        bodyClass="p-0"
      >
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th className="th">功能</th>
                <th className="th">一句话</th>
                <th className="th">关联</th>
              </tr>
            </thead>
            <tbody>
              {[
                { id: "F-EBM", name: "紧急制动管理", d: "模式×原因矩阵触发紧急制动，SIL2/4 双通道表决" },
                { id: "F-ATP", name: "超速防护", d: "速度监督阈值 EBI/SBI 分级干预" },
                { id: "F-DOOR", name: "车门联锁与级联", d: "门状态联锁发车许可，故障级联降级" },
                { id: "F-NET", name: "网络管理与完整性", d: "心跳监督 / CRC / 错误状态机" },
              ].map((f) => (
                <tr key={f.id} className="tr-hover">
                  <td className="td">
                    <code className="kbd-mono">{f.id}</code>{" "}
                    <span className="ml-1 font-medium text-ink">{f.name}</span>
                  </td>
                  <td className="td text-ink-dim">{f.d}</td>
                  <td className="td">
                    <Link to={`/graph?focus=${f.id}`} className="text-info text-xs hover:underline">
                      在图谱中查看 →
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}
