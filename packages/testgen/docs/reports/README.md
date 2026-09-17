# reports/ —— 可复现运行报告

本目录存放全链路 demo 的实测输出（JSON），每个文件对应一条可复现命令：

| 文件 | 来源命令 | 关键数字（实测） |
|---|---|---|
| demo_mock.json | `python examples/demo_full_loop.py --source mock_llm --num 12 --mutation` | num=12 小样（演示用） |
| demo_mock_full.json | `python examples/demo_full_loop.py --source mock_llm --num 40 --mutation` | 34 parsed / compile 1.0 / exec_pass 1.0 / 3 变异 kill_rate 1.0 |

报告是证据不是装饰：任何人重跑同一命令应得同一量级数字（mock 确定性 +
真实执行固定环境）。若上游 tcms-can-test 版本变更导致数字漂移，请更新本表
并说明差异（docs/decisions.md 记录）。
