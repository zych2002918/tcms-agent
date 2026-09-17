"""用法示例：批量跑多场景并对比质量。"""

from tcms_ai_testgen.models import GenRequest
from tcms_ai_testgen.pipeline import run_pipeline

scenarios = [
    GenRequest(target="TCMS EBM 紧急制动", requirements=["超速触发制动", "断线告警"], num_cases=6),
    GenRequest(target="TCMS ATP 超速防护", requirements=["超速警告", "超速干预"], hints=["EBI 曲线边界"], num_cases=6),
    GenRequest(target="CAN 错误状态机", requirements=["Bus-Off 进入与恢复"], hints=["TEC 阈值 255/256"], num_cases=4),
]

for req in scenarios:
    report = run_pipeline(req)
    s = report.summary()
    print(f"[{req.target}] parsed={s['parsed']} parse_rate={s['parse_rate']:.0%} "
          f"quality={s['quality_score']} exec_pass={s['exec_pass_rate']:.0%}")
