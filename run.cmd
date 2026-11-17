@echo off
rem Windows: double-click, or type  .\run.cmd  (options are passed on: .\run.cmd --demo, .\run.cmd --stop ...)
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (py -3 run.py %*) else (python run.py %*)
if errorlevel 1 pause
