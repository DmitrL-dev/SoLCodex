@echo off
setlocal
py -3 -c "import sys;sys.exit(sys.version_info < (3,9))" >nul 2>nul
if errorlevel 1 goto python_fallback
py -3 "%PLUGIN_ROOT%\scripts\sol_hook.py"
exit /b %errorlevel%

:python_fallback
python -c "import sys;sys.exit(sys.version_info < (3,9))" >nul 2>nul
if errorlevel 1 (
  >&2 echo Python 3.9 or newer is required for SoL Codex hooks.
  exit /b 1
)
python "%PLUGIN_ROOT%\scripts\sol_hook.py"
exit /b %errorlevel%
