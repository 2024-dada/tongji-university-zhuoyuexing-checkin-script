@echo off
cd /d "%~dp0程序文件"
start "" "%~dp0程序文件\runtime\pythonw.exe" "%~dp0程序文件\launcher.pyw"
