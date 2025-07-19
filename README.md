# AutoDeploy

A simple tool to keep your custom programs up to date by copying updated files from a source location to a target location.

## How it works

- Watches for changes to specified files (like ArkViewer.exe or ArkHandler.exe).
- When a change is detected, copies the updated file to your target location.
- Optionally restarts the target process if needed.

## Setup

1. **Install dependencies**  
   Run:
   ```
   pip install -r requirements.txt
   ```

2. **Configure your files**  
   - Copy `config-example.ini` to `config.ini`.
   - Edit `config.ini` and set the correct paths for your source and target files.

3. **Run the app**  
   ```
   python main.py
   ```
   Or use the compiled executable if you built it with PyInstaller.

## Notes

- This is a personal tool, so the config and logic are tailored for my own workflow.
- Make sure the source and target files exist before running.
- The app will log actions to `.autodeploy-logs` in the project directory.

---

