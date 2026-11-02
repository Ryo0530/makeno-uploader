@echo off
rem 負けの研究室：先週分の週次レポートと台本を作る（タスクスケジューラから呼ぶ想定）
setlocal
cd /d "%~dp0"
if not exist logs mkdir logs
set PY=python
where py >nul 2>nul && set PY=py -3
%PY% weekly_report.py build --config config.json >> logs\weekly_report.log 2>&1
set RC=%ERRORLEVEL%
echo [%date% %time%] exit=%RC% >> logs\weekly_report.log
exit /b %RC%
