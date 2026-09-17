---
name: Pull Request
about: 提交代码变更
title: ''
labels: ''
assignees: ''
---

## 变更概述
<!-- 一句话说明本 PR 做什么 -->

## 关联 Issue
<!-- Closes #N -->

## 变更内容
- [ ] 新增模块/功能
- [ ] 修复缺陷
- [ ] 文档更新
- [ ] 测试补充

## 测试验证
- [ ] 新增/修改测试用例数: 
- [ ] 全量回归通过（`python -m pytest tests/`）
- [ ] 覆盖率门禁通过（`--cov-fail-under=97`）
- [ ] ruff 检查通过（`ruff check tcms tests demo.py run.py scripts`）
- [ ] demo 冒烟通过（`python demo.py`）

## 验收说明
<!-- 与现有模块的互操作影响、README/QA 是否同步 -->
