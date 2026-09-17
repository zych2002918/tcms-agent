# -*- coding: utf-8 -*-
"""PyInstaller 打包入口脚本（用绝对导入,避免相对导入问题）。"""
from tcms_ai_platform.server.app import main

if __name__ == "__main__":
    main()
