@echo off
echo Syncing EasyBIM plugin with the latest changes from GitHub...
echo.
cd /d "%~dp0"
git pull
echo.
echo Done! Reload pyRevit in Revit to see the latest buttons (pyRevit tab ^> Reload).
pause
