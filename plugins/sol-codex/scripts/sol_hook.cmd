@echo off
setlocal
call py -3 -c "import sys;sys.exit(sys.version_info < (3,9))" >nul 2>nul
if errorlevel 1 goto python_fallback
call py -3 -c "import runpy,sys; p=sys.argv[1]; exec('try:\n runpy.run_path(p,run_name=__name__)\nexcept FileNotFoundError as e:\n if e.filename != p: raise')" "%PLUGIN_ROOT%\scripts\sol_hook.py"
exit /b %errorlevel%

:python_fallback
call python -c "import sys;sys.exit(sys.version_info < (3,9))" >nul 2>nul
if errorlevel 1 (
  >&2 echo Python 3.9 or newer is required for SoL Codex hooks.
  exit /b 1
)
call python -c "import runpy,sys; p=sys.argv[1]; exec('try:\n runpy.run_path(p,run_name=__name__)\nexcept FileNotFoundError as e:\n if e.filename != p: raise')" "%PLUGIN_ROOT%\scripts\sol_hook.py"
exit /b %errorlevel%
