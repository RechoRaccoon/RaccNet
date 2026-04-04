# RaccNet Release Notes

---

## 2026-03-25

### Desktop Shortcut on First Launch

- **First-run shortcut prompt** — When a user runs `RaccNet.exe` for the first time, they are now asked if they want a Desktop shortcut created automatically.
- **One-time only** — The prompt only appears once. After answering, a flag file is saved to `%APPDATA%\RaccNet\shortcuts_asked.flag` so it never asks again.
- **Icon bundled in exe** — The RaccNet raccoon icon is now embedded directly into `RaccNet.exe` and also extracted to `%APPDATA%\RaccNet\RaccNet Icon.ico` for use by the shortcut.
- **Path-safe** — Shortcut creation correctly handles file paths containing apostrophes and spaces (e.g. folders like `Raccoon's Projects`).
- **Build updated** — `Build_exe.bat` now passes `--icon` and bundles the `.ico` file via `--add-data` so the icon is available on any user's machine regardless of where they saved the exe.
