@echo off
cd /d "%~dp0"
if not exist "examples\demo_data\1.nc" (
    python examples\generate_demo.py
)
start "" python scan_path_viewer.py examples\demo_data
