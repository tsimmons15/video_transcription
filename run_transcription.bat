@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "DEBUG=0"

if /I "%1"=="--debug" (
    set "DEBUG=1"
    shift
)

REM ===============================
REM Configuration
REM ===============================
set MAIN=project\transcription
set ROOT_DIR=project
set FFMPEG_DIR=ffmpeg.exe
set DRIVE_CRED=api_creds
set LOG_DIR=project\log

REM ===============================
REM Bitmask input (default = 0)
REM Bit  Value  Flag
REM ---  -----  -------------------
REM 0    1      --use-drive
REM 1    2      --extract-audio
REM 2    4      --build-transcript
REM 3    8      --extract-questions
REM Usage: run.bat 15
REM or
REM Usage: run.bat all
REM ===============================
set FLAG_MASK=%1
if "%FLAG_MASK%"=="" set FLAG_MASK=0
REM Normalize FLAG_MASK to lowercase (pure batch)
REM for %%C in (A B C D E F G H I J K L M N O P Q R S T U V W X Y Z) do (
REM     set "FLAG_MASK=!FLAG_MASK:%%C=%%c!"
REM )

if /I "%FLAG_MASK%"=="all"  set FLAG_MASK=15
if /I "%FLAG_MASK%"=="audio" set FLAG_MASK=2
if /I "%FLAG_MASK%"=="transcribe" set FLAG_MASK=4
if /I "%FLAG_MASK%"=="questions" set FLAG_MASK=8
if /I "%FLAG_MASK%"=="none" set FLAG_MASK=0

REM Collect remaining args (2..N) into EXTRA_ARGS
set "EXTRA_ARGS="
shift
:collect_args
if "%~1"=="" goto done_collect
set "EXTRA_ARGS=!EXTRA_ARGS! %~1"
shift
goto collect_args
:done_collect


REM ===============================
REM Decode bitmask
REM ===============================
set "FLAGS="

REM bit 0 -> 1
set /a TEST=!FLAG_MASK! ^& 1
if !TEST! NEQ 0 set "FLAGS=!FLAGS! --use-drive"

REM bit 1 -> 2
set /a TEST=!FLAG_MASK! ^& 2
if !TEST! NEQ 0 set "FLAGS=!FLAGS! --extract-audio"

REM bit 2 -> 4
set /a TEST=!FLAG_MASK! ^& 4
if !TEST! NEQ 0 set "FLAGS=!FLAGS! --build-transcript"

REM bit 3 -> 8
set /a TEST=!FLAG_MASK! ^& 8
if !TEST! NEQ 0 set "FLAGS=!FLAGS! --extract-questions"

if "%DEBUG%" == 1 (
	echo "DEBUG:"
	echo RAW_ARG1=%1
	echo FLAG_MASK=%FLAG_MASK%
	echo FLAGS=%FLAGS%
	echo EXTRA_ARGS=%EXTRA_ARGS%
)

REM ===============================
REM Execute
REM  Defaults for --root-dir, etc. are handled by argparse handling
REM  If two --root-dir are present, argparse returns the last
REM ===============================
python "%MAIN%\main.py" ^
  --root-dir "%ROOT_DIR%" ^
  --ffmpeg_bin "%FFMPEG_DIR%" ^
  --drive-creds "%DRIVE_CRED%" ^
  --log-dir "%LOG_DIR%" ^
  %FLAGS% ^
  %EXTRA_ARGS%

endlocal
