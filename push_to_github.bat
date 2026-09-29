@echo off
title Push PosturFix ke GitHub
echo ========================================================
echo   Sedang melakukan Push PosturFix ke GitHub...
echo   Target: https://github.com/andregooner/posturfix.git
echo ========================================================
echo.
git push -u origin main
echo.
if %ERRORLEVEL% equ 0 (
    echo [SUKSES] Berhasil di-push ke GitHub!
) else (
    echo [GAGAL] Terjadi kesalahan saat push.
)
echo.
pause
