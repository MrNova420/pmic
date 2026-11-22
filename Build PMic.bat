@echo off
rem Builds the portable, fully self-contained PMic for win64:
rem   dist\PMic\PMic.exe   + web\ assets + bundled VB-CABLE driver
rem   PMic-<version>-portable-win64.zip
rem For an installer, run "Build MSI.bat" instead (it calls this build).
setlocal
cd /d "%~dp0"

if exist build rmdir /s /q build >nul 2>&1
if exist dist\PMic rmdir /s /q dist\PMic >nul 2>&1

python tools\make_version_info.py || goto :err
python tools\make_icon.py || goto :err

set "VER="
set /p VER=<build\version.txt
if "%VER%"=="" (
  echo Could not read the version from tools\make_version_info.py output.
  exit /b 1
)

python -m PyInstaller PMic.spec --noconfirm || goto :err

set "ZIP=PMic-%VER%-portable-win64.zip"
if exist "%ZIP%" del "%ZIP%"
powershell -NoProfile -Command "Compress-Archive -Path 'dist\PMic\*' -DestinationPath '%ZIP%' -Force" || goto :err

python tools\check_bundle.py "dist\PMic" || goto :err

echo.
echo Done: dist\PMic\PMic.exe   ^(portable folder, no Python needed^)
echo Zip:  %ZIP%
echo.
echo Next: "Build MSI.bat" to wrap this build into an installer.
goto :eof

:err
echo.
echo Build failed - see the output above.
exit /b 1
