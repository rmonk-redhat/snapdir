# Gemini CLI Instructions for Snapdir

## Project Overview
Snapdir is a cross-platform Python tool designed to snapshot a set of directories and upload them securely to an SFTP server. It features a graphical user interface (GUI) built with `tkinter`.

## Core Technologies
- **Python**: Core programming language.
- **tkinter**: Used for the graphical user interface.
- **paramiko**: Used for handling SSH/SFTP connections.
- **tarfile**: Used for archiving directories before uploading.

## Rules & Guidelines

### 1. Cross-Platform Compatibility
- Ensure all file path operations use `os.path` or `pathlib` to maintain compatibility across Windows, macOS, and Linux.
- Do not use OS-specific commands or hardcoded file separators.

### 2. GUI Threading (tkinter)
- **Never block the main thread.** Any long-running operations (like tarball creation, SFTP uploads, or hashing) must be executed in a separate background thread (e.g., using `threading.Thread`) to keep the UI responsive.
- When updating the GUI from a background thread, ensure thread safety or use appropriate Tkinter mechanisms (like `root.after()`).

### 3. Dependencies
- Rely on standard library modules whenever possible to minimize external dependencies.
- The primary external dependency is `paramiko`. Always verify before adding any new third-party libraries.

### 4. Configuration
- The tool uses a `config.json` file for configuration.
- Any changes to the required configuration must also be reflected in `config.json.example`.
- Never commit actual configuration files with credentials. Only commit the `.example` files.

### 5. Security & Connectivity
- Handle SSH host keys carefully. The custom `GUIHostKeyPolicy` prompts users before accepting unknown host keys. Do not bypass this security feature.
- **Robustness**: When loading system host keys (e.g., via `ssh.load_system_host_keys()`), always wrap the call in a `try...except` block to ensure that malformed or invalid entries in the `known_hosts` file do not cause the application to crash. These should be simply ignored.
- Never log or print passwords or private keys.

### 6. Code Style
- Follow PEP 8 guidelines for Python code style and formatting.
- Keep the code straightforward and maintainable.
