@echo off
rem ===========================================================================
rem  TCMS x AI - one-click start (Web UI)
rem
rem  This wrapper stays ASCII-only on purpose: cmd.exe parses .bat files using
rem  the OEM code page, so non-ASCII text here would be mangled. All Chinese
rem  messages and the actual logic live in start.ps1, which is UTF-8 with BOM
rem  so that Windows PowerShell 5.1 decodes it correctly.
rem ===========================================================================
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1"
