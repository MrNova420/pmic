@echo off
rem Builds the standalone PMic app into dist\PMic (and a zip for sharing).
cd /d "%~dp0"
python -m PyInstaller PMic.spec --noconfirm || goto :err
powershell -NoProfile -Command "Compress-Archive -Path 'dist\PMic\*' -DestinationPath 'PMic-1.0.0-win64.zip' -Force"
echo.
echo Done: dist\PMic\PMic.exe   (portable folder, no Python needed on the target PC)
echo Zip:  PMic-1.0.0-win64.zip
goto :eof
:err
echo Build failed - see the output above.
exit /b 1
