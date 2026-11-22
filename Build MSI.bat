@echo off
rem Wraps the portable build into a Windows Installer package (MSI).
rem   PMic-Setup-<version>-x64.msi
rem Requires WiX 4+ as a dotnet tool:  dotnet tool install --global wix
setlocal
cd /d "%~dp0"
set "PATH=%PATH%;%USERPROFILE%\.dotnet\tools"

where wix >nul 2>&1 || (
  echo WiX is not installed. Install it with:
  echo     dotnet tool install --global wix
  exit /b 1
)

rem Always rebuild the portable app first. Packaging whatever happens to be
rem in dist\ is how an installer ends up shipping last week's binary.
call "%~dp0Build PMic.bat" || exit /b 1

python tools\make_installer.py || goto :err

set "VER="
set /p VER=<build\version.txt
set "MSI=PMic-Setup-%VER%-x64.msi"

rem the license page needs the UI extension. Pin it to the WiX 5 line:
rem WiX 6+ refuses to run without accepting their Open Source Maintenance Fee
rem EULA, which is not something a build script should impose on you.
wix extension add -g WixToolset.UI.wixext/5.0.2 >nul 2>&1
wix extension add -g WixToolset.UI.wixext >nul 2>&1
wix build installer\PMic.wxs -arch x64 -b src=dist\PMic -ext WixToolset.UI.wixext -o "%MSI%" || goto :err

python tools\make_checksums.py "%MSI%" "PMic-%VER%-portable-win64.zip" || goto :err

echo.
echo Done: %MSI%
echo.
echo Install it with a double-click, or silently:
echo     msiexec /i "%MSI%" /qn
goto :eof

:err
echo.
echo MSI build failed - see the output above.
exit /b 1
