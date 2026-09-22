import { useEffect, useState } from "react";
import { api, type ScenarioComposition } from "../api";
import { Tag } from "./ui";
import { IconInfo } from "./icons";

/**
 * 「为什么注入了多个故障」解释卡。
 *
 * ## 它解决的具体问题
 *
 * 用户在某故障上点"看动画"，动画里却注入了三个故障（SOC 偏低、电池温度偏高、
 * 车门传感器偶发噪声），于是产生疑问：是**前置条件**还是**多余注入**？
 * 答案是第三种：**它们本来就是同一个场景里并列编排的步骤**。
 *
 * 全库 104 个场景里有 61 个是多故障（最多 6 个），所以这不是给某一个场景打的
 * 补丁，而是所有多故障场景都走这一张卡。演示现场被问到时可以直接指着屏幕念
 * `oneliner`，不必临场解释。
 *
 * 数据全部来自场景 YAML 与故障字典（`/api/scenarios/{file}/composition`），
 * 不含推断；"无前置依赖"这一句也是可证明的——场景步骤是扁平序列，
 * 数据模型里没有任何表达故障间依赖的字段。
 */
export function ScenarioCompositionCard({
  scenario,
  entryFault,
}: {
  scenario: string;
  entryFault?: string;
}) {
  const [comp, setComp] = useState<ScenarioComposition | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let alive = true;
    if (!scenario) return;
    api
      .scenarioComposition(scenario, entryFault)
      .then((c) => alive && setComp(c))
      .catch(() => alive && setComp(null));
    return () => {
      alive = false;
    };
  }, [scenario, entryFault]);

  if (!comp || !comp.available) return null;
  // 两种情况都值得解释：
  //  - is_multi      多个不同故障（"点一个却注入三个"）
  //  - repeat_single 同一个故障被注入多次（"为什么又注入一次"）
  if (!comp.is_multi && comp.relation !== "repeat_single") return null;

  const entry = comp.injections.find((i) => i.role === "entry");
  const co = comp.injections.filter((i) => i.role === "co");
  const headline = comp.is_multi
    ? `本场景共注入 ${comp.total_faults} 个故障${
        comp.total_injections !== comp.total_faults ? `（${comp.total_injections} 次）` : ""
      }`
    : `本场景把同一个故障注入了 ${comp.total_injections} 次`;

  return (
    <div className="rounded-[var(--radius-md)] border border-info/30 bg-info/8 px-3 py-2.5 step-in">
      <div className="flex flex-wrap items-center gap-2">
        <span className="inline-flex items-center gap-1.5 text-info text-sm font-medium"><IconInfo className="h-4 w-4" />{headline}</span>
        <span className="text-[11px] text-ink-faint">
          {comp.is_multi
            ? entry
              ? "你点进来的那个只是其中之一"
              : "它们并列编排在同一场景里"
            : "「恢复后重发」型编排，不是多个不同故障"}
        </span>
        <button
          type="button"
          className="ml-auto text-[11px] text-info hover:underline decoration-dotted shrink-0"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
        >
          {open ? "收起说明" : "为什么会这样？"}
        </button>
      </div>

      <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
        {entry ? (
          <span className="inline-flex items-center gap-1.5">
            <Tag tone="bad">你点的</Tag>
            <span className="text-ink">{entry.fault_name || entry.fault}</span>
            <span className="text-ink-faint">
              （{entry.level_label}级 · 期望{entry.expect_label}）
            </span>
          </span>
        ) : null}
        {co.length ? (
          <span className="inline-flex flex-wrap items-center gap-1.5">
            <Tag tone="dim">同场景一并注入</Tag>
            {co.map((i) => (
              <span key={i.fault} className="text-ink-dim">
                {i.fault_name || i.fault}
                <span className="text-ink-faint">
                  （@{i.at.toFixed(1)}s · {i.level_label}级 · 期望{i.expect_label}）
                </span>
              </span>
            ))}
          </span>
        ) : null}
      </div>

      {open ? (
        <div className="mt-2 border-line/60 border-t pt-2 text-xs">
          <p className="text-ink-dim leading-6">{comp.why}</p>
          <div className="mt-1.5 rounded-[var(--radius-sm)] bg-surface-2/60 px-2.5 py-1.5">
            <div className="text-[11px] text-ink-faint">演示口径（可直接照读）</div>
            <div className="text-ink-dim mt-0.5">{comp.oneliner}</div>
          </div>
          {comp.desc ? (
            <p className="mt-1.5 text-ink-faint">场景自述：{comp.desc}</p>
          ) : null}
          <table className="mt-2 w-full text-[11px]">
            <thead>
              <tr className="text-ink-faint text-left border-b border-line-soft">
                <th className="font-normal py-1">时刻</th>
                <th className="font-normal py-1">故障</th>
                <th className="font-normal py-1">等级</th>
                <th className="font-normal py-1">期望处置</th>
                <th className="font-normal py-1">角色</th>
              </tr>
            </thead>
            <tbody>
              {comp.injections.map((i) => (
                <tr key={i.fault} className="text-ink-dim border-b border-line-soft/60 last:border-0">
                  <td className="py-1 tabular-nums num">@{i.at.toFixed(1)}s</td>
                  <td>
                    {i.fault_name || i.fault}{" "}
                    <code className="kbd-mono text-ink-faint">{i.fault}</code>
                  </td>
                  <td>{i.level_label}</td>
                  <td>{i.expect_label}</td>
                  <td className={i.role === "entry" ? "text-bad" : "text-ink-faint"}>{i.role_label}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {comp.recoveries.length ? (
            <p className="mt-1.5 text-ink-faint">
              恢复时刻：
              {comp.recoveries.map((r) => `${r.fault}@${r.at.toFixed(1)}s`).join("、")}
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
