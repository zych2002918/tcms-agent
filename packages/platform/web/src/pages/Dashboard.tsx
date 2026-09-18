import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type Stats } from "../api";
import { Panel, Tag, StatusDot, StatCard, Callout, SkeletonRows } from "../components/ui";

/** 资产源：把后端给的路径说成人话（完整路径收进 title / 细节层，不占首屏一行） */
function assetSourceLabel(src?: string | null): string {
  if (!src) return "读取中…";
  return src.startsWith("bundled:") ? "内置快照" : "外部引擎目录";
}

/**
 * 总览页。信息顺序按首屏决策排：**状态 → 入口 → 数据明细**。
 *
 * 三处刻意为之：
 * 1. 入口有主次——平台最想让你干的事（把任务交给 AI 测试 Agent）单独一张强调卡，
 *    其余三个入口并列次要；四个等权的卡片等于没有推荐。
 * 2. 数字一律中性色（语义色只留给"运行中/异常"这类状态），层级交给字阶；
 * 3. "这些数字怎么来的"收进 Callout 的一句话 + 可展开细节，不再往页面里贴开发说明。
 */
export function Dashboard() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [health, setHealth] = useState<{ status: string; version: string; engine_version: string | null } | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    api.stats().then(setStats).catch((e) => setErr(String(e)));
    api.health().then(setHealth).catch(() => undefined);
  }, []);

  const platformOk = health?.status === "ok";
  const engineReady = !!health?.engine_version;

  /** 次要入口：主入口之外的三个分场景入口（顺序 = 真实执行的完整度：跑 → 看 → 查） */
  const secondary = [
    {
      to: "/scenarios",
      icon: "▶",
      title: "跑一个故障场景",
      desc: "挑一个现成场景，先看它编排了什么，再让 TCMS 引擎真跑一遍看断言。",
      cta: "去执行场景",
    },
    {
      to: "/faultlab",
      icon: "⚙",
      title: "看故障如何发生",
      desc: "选一个真实故障，用动画看它如何被检测、系统如何处置——每个事件都可溯源。",
      cta: "去故障演示",
    },
    {
      to: "/graph",
      icon: "◈",
      title: "问 TCMS 领域知识",
      desc: "输入「车门故障不能发车」这类问题，返回带证据链的答案。",
      cta: "去知识图谱",
    },
  ];

  /** 资产速览六卡：点一张直达「测试资产」对应分区 */
  const cards = [
    { to: "/assets?tab=messages", value: stats?.messages, label: "报文 (DBC)", hint: "DBC 协议库定义的报文数" },
    { to: "/assets?tab=signals", value: stats?.signals, label: "信号", hint: "报文内含的信号数" },
    { to: "/assets?tab=faults", value: stats?.faults, label: "故障 (FMEA)", hint: "可注入的故障字典条目" },
    { to: "/assets?tab=scenarios", value: stats?.scenarios, label: "场景", hint: "可真实执行的故障场景" },
    { to: "/assets?tab=requirements", value: stats?.req_ids, label: "安全需求", hint: "RTM 追溯矩阵需求数" },
    { to: "/assets?tab=functions", value: stats?.functions, label: "被测功能", hint: "列车视角功能聚合" },
  ];

  return (
    <div className="mx-auto w-full max-w-[1800px] space-y-4">
      {err && (
        <div className="panel border-bad/40 bg-bad/10 px-4 py-2.5 text-sm text-bad">⚠ 无法连接后端：{err}</div>
      )}

      {/* ① 状态：平台 / 引擎 / 资产源。路径不再是首屏正文，只说人话 */}
      <Panel title="系统状态" bodyClass="py-3">
        <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-[13px]">
          <span className="flex items-center gap-2">
            <StatusDot tone={platformOk ? "ok" : "bad"} pulse />
            <span>
              {health === null ? "连接中…" : platformOk ? "平台运行中" : "平台异常"} · v
              {stats?.version ?? "–"}
            </span>
          </span>
          <span className="flex items-center gap-1.5">
            <span className="text-ink-dim">TCMS 引擎：</span>
            {engineReady ? (
              <span className="text-ink">
                v<span className="num">{health?.engine_version}</span>
              </span>
            ) : (
              <span className="text-warn">未接入（场景执行需要它）</span>
            )}
          </span>
          <span className="flex items-center gap-1.5 text-ink-dim" title={stats?.source_upstream ?? "资产源读取中…"}>
            当前资产源：<Tag tone="dim">{assetSourceLabel(stats?.source_upstream)}</Tag>
          </span>
        </div>
      </Panel>

      {/* ② 从这里开始：主入口一张强调卡，其余三个并列次要 */}
      <section>
        <div className="mb-2 flex flex-wrap items-baseline gap-x-2 px-1">
          <h2 className="section-title">从这里开始</h2>
          <span className="text-[11px] text-ink-faint">第一次来，先试第一张卡；下面三个是它的分场景入口</span>
        </div>

        <Link to="/agent" className="panel panel-hover group block border-info/40 bg-info/5 p-4">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:gap-6">
            <div className="flex min-w-0 items-start gap-3 lg:flex-1">
              <span className="grid h-10 w-10 shrink-0 place-items-center rounded-[var(--radius-md)] border border-info/40 bg-info/10 text-[18px] text-info">
                ✦
              </span>
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-[15px] font-medium text-ink">指挥 AI 测试 Agent</span>
                  <Tag tone="info">推荐从这里开始</Tag>
                </div>
                <p className="mt-1 text-[12.5px] leading-5 text-ink-dim">
                  用大白话给一个目标（例如「车门故障了还能发车吗」），它自己检索证据、在真实引擎上执行，并把每一步摊开给你看。
                </p>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {["白盒轨迹", "真实引擎执行", "报告标注所用模型"].map((t) => (
                    <Tag key={t} tone="dim">
                      {t}
                    </Tag>
                  ))}
                </div>
              </div>
            </div>
            <span className="inline-flex shrink-0 items-center gap-1 text-[13px] text-info">
              去 Agent 工作台
              <span className="transition-transform group-hover:translate-x-0.5" aria-hidden>
                →
              </span>
            </span>
          </div>
        </Link>

        <div className="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {secondary.map((a) => (
            <Link key={a.to} to={a.to} className="panel panel-hover group flex flex-col p-4">
              <span className="grid h-9 w-9 place-items-center rounded-[var(--radius-md)] border border-line bg-surface-2 text-[16px] text-ink-dim">
                {a.icon}
              </span>
              <div className="mt-3 text-[14px] font-medium text-ink">{a.title}</div>
              <p className="mt-1 text-[12.5px] leading-5 text-ink-dim">{a.desc}</p>
              <span className="mt-auto inline-flex items-center gap-1 pt-3 text-[12px] text-info">
                {a.cta}
                <span className="transition-transform group-hover:translate-x-0.5" aria-hidden>
                  →
                </span>
              </span>
            </Link>
          ))}
        </div>
      </section>

      {/* ③ 数据明细：资产速览（中性数字）+ 被测功能表 */}
      <section>
        <div className="mb-2 flex flex-wrap items-baseline gap-x-2 px-1">
          <h2 className="section-title">资产速览</h2>
          <span className="text-[11px] text-ink-faint">点一张卡直达「测试资产」的对应分区</span>
        </div>

        {stats === null && !err ? (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6" aria-hidden>
            {cards.map((c) => (
              <div key={c.label} className="panel px-3.5 py-3">
                <SkeletonRows rows={2} cols={1} />
              </div>
            ))}
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
            {cards.map((c) => (
              <Link
                key={c.label}
                to={c.to}
                /* Link 里放 StatCard：保住键盘可达的外链语义，同时把卡面复用共享基元 */
                className="block rounded-[var(--radius-lg)] [&>.panel]:transition-colors [&>.panel]:hover:border-ink-faint/35"
              >
                <StatCard value={c.value ?? "–"} label={c.label} hint={`${c.hint}（点击直达测试资产）`} />
              </Link>
            ))}
          </div>
        )}

        <Callout
          tone="dim"
          icon="◎"
          className="mt-3"
          title="这些数字是从真实资产里数出来的"
          details={
            <>
              <div>
                资产源：
                {stats?.source_upstream ? (
                  <code className="kbd-mono">{stats.source_upstream}</code>
                ) : (
                  "读取中…"
                )}
              </div>
              <div className="mt-1">
                报文与信号来自 DBC，故障来自 faults.yaml，场景来自场景目录，需求来自 RTM 追溯矩阵；
                换一份资产文件，这里的数字立刻跟着变——页面里没有写死任何一个。
              </div>
            </>
          }
        >
          后端加载资产时现算的，不是页面上的展示数字。
        </Callout>
      </section>

      <Panel title="被测功能" sub="列车视角的测试对象" bodyClass="p-0">
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
