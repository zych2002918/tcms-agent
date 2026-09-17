@echo off
rem ASCII-only wrapper (cmd.exe parses .bat with the OEM code page).
rem All Chinese text lives in the UTF-8-with-BOM .ps1 file.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0test.ps1"
