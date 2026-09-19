# miniGit

miniGit is a small Python implementation of core Git ideas. It stores Git-like
objects on disk, keeps a staging index, creates commits, and supports simple
branch workflows from a command-line interface.

This project is intended as a learning-focused version control tool rather than
a full Git replacement.

## Features

- Initialize a repository with a `.git` directory structure.
- Store compressed SHA-1 objects for blobs, trees, and commits.
- Add individual files or entire directories to the staging index.
- Create commits with messages and optional author metadata.
- Track parent commits for linear history.
- List commit history for the current branch.
- Create, list, delete, and checkout branches.
- Restore tracked files when switching branches.
- Show repository status, including staged, unstaged, untracked, and deleted
  files.

## Requirements

- Python 3.10 or newer

The project only uses the Python standard library, so no dependency install step
is required.

## Startup Commands

Run commands from the project root:

```bash
cd /home/shreyan-naskar/shreyan-naskar/github/miniGit
```

Show the CLI help:

```bash
python3 main.py --help
```

Initialize a miniGit repository:

```bash
python3 main.py init
```

Add files or directories to the staging area:

```bash
python3 main.py add main.py src
```

Create a commit:

```bash
python3 main.py commit -m "Initial commit"
```

Create a commit with explicit author metadata:

```bash
python3 main.py commit -m "Add repository commands" --author "Your Name <you@example.com>"
```

Check repository status:

```bash
python3 main.py status
```

View recent commits:

```bash
python3 main.py log
python3 main.py log -n 5
```

List branches:

```bash
python3 main.py branch
```

Create a branch from the current commit:

```bash
python3 main.py branch feature-name
```

Create and switch to a new branch:

```bash
python3 main.py checkout -b feature-name
```

Switch to an existing branch:

```bash
python3 main.py checkout feature-name
```

Delete a branch:

```bash
python3 main.py branch -d feature-name
```

## Project Structure

```text
.
├── main.py
└── src
    ├── __init__.py
    ├── cli.py
    ├── objects.py
    └── repository.py
```

- `main.py` starts the CLI.
- `src/cli.py` defines command-line arguments and dispatches commands.
- `src/objects.py` implements blob, tree, and commit object serialization.
- `src/repository.py` implements repository storage, staging, commits, branches,
  checkout, log, and status behavior.

