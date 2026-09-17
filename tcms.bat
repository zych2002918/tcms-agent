@echo off
rem ===========================================================================
rem  TCMS x AI - command line entry (wrapper for `uv run tcms-agent`)
rem
rem  Why this exists: the bundled copy ships its own uv, and a brand-new machine
rem  usually has NO global `uv` on PATH. Telling a first-time user to type
rem  `uv run tcms-agent ...` therefore fails with
rem      'uv' is not recognized as an internal or external command
rem  and even when uv exists, running it from another directory fails with
rem      error: Failed to spawn: `tcms-agent` / program not found
rem  This wrapper locates uv (PATH first, then the bundled _offline\uv.exe),
rem  switches to the package directory, and forwards every argument.
rem
rem  ASCII-only on purpose: cmd.exe parses .bat with the OEM code page.
rem ===========================================================================
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0_tools\tcms.ps1" %*
