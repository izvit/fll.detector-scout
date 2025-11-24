DetectorScout — Minimal PySide6 Grid Canvas Demo

Quick start

1. Create and activate a virtual environment (Windows PowerShell):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

2. Install dependencies declared in `pyproject.toml`:

```powershell
pip install -U pip
pip install "PySide6>=6.5"
```

3. Run the demo:

```powershell
python main.py
```

What it contains

- `main.py`: A small PySide6 application with a `GridCanvas` widget that draws a simple grid. Use the `Cell size` spinner to change the grid spacing.

Notes

- This project uses `PySide6` for a modern, cross-platform native UI. Adjust the dependency version if you need a specific release.
