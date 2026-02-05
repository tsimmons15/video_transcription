# Video Transcription

This project is meant to be used as a tool to work with mock interview practicing.

Built with Python: Python 3.11.9
FFMPEG: 
ffmpeg version 8.0.1-full_build-www.gyan.dev Copyright (c) 2000-2025 the FFmpeg developers
  built with gcc 15.2.0 (Rev8, Built by MSYS2 project)

Download the project to a specific directory and modify the run_transcription.bat to update:
```batch
REM ===============================
REM Configuration
REM ===============================
set MAIN=/path/to/main.py
set ROOT_DIR=/path/to/data/repository
set FFMPEG_DIR=/path/to/ffmpeg.exe
set DRIVE_CRED=/path/to/the/various/api/credentials
set LOG_DIR=/path/to/your/log/directory
```

My recommended project directory:
root/
\- data/
   \- videos/
   \- transcripts/
   \- questions/
