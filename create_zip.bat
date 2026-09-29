@echo off
echo ============================================================
echo  Mempersiapkan File ZIP PosturFix untuk Dibagikan
echo ============================================================
.venv\Scripts\python.exe -c "import shutil, os; shutil.make_archive('PosturFix_v1.0_Windows', 'zip', root_dir='dist', base_dir='PosturFix'); print('\n[SUKSES] File siap dibagikan: PosturFix_v1.0_Windows.zip (' + str(round(os.path.getsize('PosturFix_v1.0_Windows.zip')/(1024*1024), 2)) + ' MB)')"
echo.
pause
