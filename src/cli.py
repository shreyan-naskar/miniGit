from __future__ import annotations

import argparse
import sys

from .repository import Repository


def build_parser() -> argparse.ArgumentParser:
    # Parser construction is separate from execution so the CLI shape is easy
    # to inspect or test without running a repository command.
    parser = argparse.ArgumentParser(description="PyGit - A simple git clone!")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # init is the bootstrap command; it creates repository metadata.
    subparsers.add_parser("init", help="Initialize a new repository")

    # add stages one or more files/directories by writing blob objects and
    # recording path -> blob-hash entries in the index.
    add_parser = subparsers.add_parser(
        "add", help="Add files and directories to the staging area"
    )
    add_parser.add_argument("paths", nargs="+", help="Files and directories to add")

    # commit turns the staged index into tree objects and then a commit object.
    commit_parser = subparsers.add_parser("commit", help="Create a new commit")
    commit_parser.add_argument(
        "-m",
        "--message",
        help="Commit message",
        required=True,
    )
    commit_parser.add_argument(
        "--author",
        help="Author name and email",
    )

    # checkout either switches to an existing branch or creates one first with
    # -b/--create-branch, then restores the working directory.
    checkout_parser = subparsers.add_parser("checkout", help="Move/Create a new branch")
    checkout_parser.add_argument("branch", help="Branch to switch to")
    checkout_parser.add_argument(
        "-b",
        "--create-branch",
        action="store_true",
        help="Create and switch to a new branch",
    )

    # branch with no name lists branches, with a name creates a branch, and with
    # -d deletes the named branch ref.
    branch_parser = subparsers.add_parser("branch", help="List or manage branches")
    branch_parser.add_argument("name", nargs="?")
    branch_parser.add_argument(
        "-d",
        "--delete",
        action="store_true",
        help="Delete the branch",
    )

    # log starts at the current branch tip and follows parent commits backward.
    log_parser = subparsers.add_parser("log", help="Show commit history")
    log_parser.add_argument(
        "-n",
        "--max-count",
        type=int,
        default=10,
        help="Limit commits shown",
    )

    # status compares last commit, index, and working directory.
    subparsers.add_parser("status", help="Show repository status")

    return parser


def main(argv: list[str] | None = None):
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return

    repo = Repository()
    try:
        if args.command == "init":
            if not repo.init():
                print("Repository already exists")
                return
        elif args.command == "add":
            # Every command except init requires repository metadata to exist.
            if not repo.git_dir.exists():
                print("Not a git repository")
                return

            for path in args.paths:
                repo.add_path(path)
        elif args.command == "commit":
            if not repo.git_dir.exists():
                print("Not a git repository")
                return

            author = args.author or "PyGit user <user@pygit.com>"
            repo.commit(args.message, author)
        elif args.command == "checkout":
            if not repo.git_dir.exists():
                print("Not a git repository")
                return
            repo.checkout(args.branch, args.create_branch)
        elif args.command == "branch":
            if not repo.git_dir.exists():
                print("Not a git repository")
                return

            repo.branch(args.name, args.delete)
        elif args.command == "log":
            if not repo.git_dir.exists():
                print("Not a git repository")
                return

            repo.log(args.max_count)
        elif args.command == "status":
            if not repo.git_dir.exists():
                print("Not a git repository")
                return

            repo.status()

    except Exception as e:
        # Keep errors user-facing at the CLI boundary instead of exposing a
        # Python traceback for normal command failures.
        print(f"Error: {e}")
        sys.exit(1)
