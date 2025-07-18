# AutoDeploy

A simple tool to keep your custom programs up to date by copying updated files from a source location to a target location.

## How it works

- Reads `config.ini` for the list of programs and their source/target paths.
- Compares files and updates the target if the source has changed.
- Optionally kills running processes before updating.

## Usage

1. Edit `config.ini` to set your program names and file paths.
2. Run `main.py` (or the compiled executable).
3. The tool will handle updating your programs automatically.

---
This project is intended for personal use to automate updates of custom programs.
