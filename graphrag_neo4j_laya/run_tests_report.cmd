@echo off
rem Runs the test suite and writes a Markdown report with every test's
rem status and the reason for each FAIL / ERROR / XFAIL / XPASS / SKIP.
rem
rem Asks two questions (Enter takes the default):
rem   live tests (real Laya model)?   [e/H]  default: no
rem   scope: planner or all tests?    [P/t]  default: planner (tests\test_planner*.py)
rem
rem Usage:  run_tests_report.cmd [report.md] [options] [extra pytest args...]
rem   options skip the matching question: --live | --no-live | --planner | --all
rem   default report: test-reports\test_report_<yyyyMMdd_HHmmss>.md

setlocal
cd /d "%~dp0"

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "TS=%%i"
set "REPORT=test-reports\test_report_%TS%.md"
set "ARG1=%~1"
if not "%~1"=="" if not "%ARG1:~0,1%"=="-" (
    set "REPORT=%~1"
    shift
)

set "LIVE="
set "SCOPE="
set "EXTRA="
:collect
if "%~1"=="" goto ask
if /i "%~1"=="--live"    (set "LIVE=y" & shift & goto collect)
if /i "%~1"=="--no-live" (set "LIVE=n" & shift & goto collect)
if /i "%~1"=="--planner" (set "SCOPE=p" & shift & goto collect)
if /i "%~1"=="--all"     (set "SCOPE=a" & shift & goto collect)
set "EXTRA=%EXTRA% %1"
shift
goto collect

:ask
if not defined LIVE set /p "LIVE=Live testler calissin mi (gercek Laya modeli)? [e/H]: "
if not defined LIVE set "LIVE=n"
if /i "%LIVE%"=="e" set "LIVE=y"
if /i not "%LIVE%"=="y" set "LIVE=n"

if not defined SCOPE set /p "SCOPE=Kapsam: planner testleri mi, tum testler mi? [P/t]: "
if not defined SCOPE set "SCOPE=p"
if /i "%SCOPE%"=="t" set "SCOPE=a"
if /i not "%SCOPE%"=="a" set "SCOPE=p"

if "%SCOPE%"=="a" (
    set "TARGETS=tests"
    set "SCOPE_NAME=tum testler"
) else (
    set "TARGETS=tests\test_planner.py tests\test_planner_executor.py tests\test_planner_live.py"
    set "SCOPE_NAME=planner"
)
if "%LIVE%"=="y" (
    set "LIVE_ARGS=--live"
    set "LIVE_NAME=evet"
) else (
    set "LIVE_ARGS=-m "not live""
    set "LIVE_NAME=hayir"
)

echo.
echo Kapsam: %SCOPE_NAME%   Live: %LIVE_NAME%
echo.

set "PYTHONPATH=%~dp0scripts;%~dp0;%PYTHONPATH%"
set "PYTHONIOENCODING=utf-8"

python -m pytest %TARGETS% -rfEsxX %LIVE_ARGS% -p md_report_plugin "--md-report=%REPORT%" %EXTRA%
set "RC=%ERRORLEVEL%"

echo.
if exist "%REPORT%" (
    echo Report: %CD%\%REPORT%
) else (
    echo Report was not written.
)
endlocal & exit /b %RC%
