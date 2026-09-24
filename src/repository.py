from __future__ import annotations

import json
from pathlib import Path
import time
from typing import Dict, List

from .objects import Blob, Commit, GitObject, Tree


# Repository is the coordinator for every on-disk concern. It owns the paths to
# the object database, refs, HEAD, and index, then exposes higher-level methods
# that implement Git-like commands.
class Repository:
    def __init__(self, path="."):
        self.path = Path(path).resolve()

        # All version-control metadata lives under mini-git, while normal project
        # files stay in the working directory.
        self.git_dir = self.path / "mini-git"
        self.objects_dir = self.git_dir / "objects"
        self.ref_dir = self.git_dir / "refs"
        self.heads_dir = self.ref_dir / "heads"
        self.head_file = self.git_dir / "HEAD"
        self.index_file = self.git_dir / "index"

    def init(self) -> bool:
        if self.git_dir.exists():
            return False

        # Minimal repository bootstrap: object storage, branch refs, HEAD, and
        # an empty staging index.
        self.git_dir.mkdir()
        self.objects_dir.mkdir()
        self.ref_dir.mkdir()
        self.heads_dir.mkdir()
        self.head_file.write_text("ref: refs/heads/master\n")
        self.save_index({})

        print(f"Initialized empty Git repository in {self.git_dir}")
        return True

    def store_object(self, obj: GitObject) -> str:
        obj_hash = obj.hash()

        # Loose object layout: first two hash characters are the directory, and
        # the rest are the filename. This avoids one huge objects directory.
        obj_dir = self.objects_dir / obj_hash[:2]
        obj_file = obj_dir / obj_hash[2:]

        if not obj_file.exists():
            # Objects are immutable. If an object with this content hash already
            # exists, there is nothing to rewrite.
            obj_dir.mkdir(exist_ok=True)
            obj_file.write_bytes(obj.serialize())

        return obj_hash

    def load_object(self, obj_hash: str) -> GitObject:
        # Reverse of store_object: derive the path from the hash, read the
        # compressed bytes, and deserialize them into a GitObject.
        obj_dir = self.objects_dir / obj_hash[:2]
        obj_file = obj_dir / obj_hash[2:]

        if not obj_file.exists():
            raise FileNotFoundError(f"Object {obj_hash} not found")

        return GitObject.deserialize(obj_file.read_bytes())

    def load_index(self) -> Dict[str, str]:
        # The index is the staging area. It maps repository-relative paths to
        # blob hashes for the content selected for the next commit.
        if not self.index_file.exists():
            return {}

        try:
            return json.loads(self.index_file.read_text())
        except:
            return {}

    def save_index(self, index: Dict[str, str]):
        self.index_file.write_text(json.dumps(index, indent=2))

    def add_file(self, path: str):
        full_path = self.path / path
        if not full_path.exists():
            raise FileNotFoundError(f"Path {path} not found")

        # Staging a file is two steps: store its content as a blob, then point
        # the index entry for this path at that blob.
        content = full_path.read_bytes()
        blob = Blob(content)
        blob_hash = self.store_object(blob)
        index = self.load_index()
        index[path] = blob_hash
        self.save_index(index)

        print(f"Added {path}")

    def add_directory(self, path: str):
        full_path = self.path / path
        if not full_path.exists():
            raise FileNotFoundError(f"Directory {path} not found")
        if not full_path.is_dir():
            raise ValueError(f"{path} is not a directory")

        index = self.load_index()
        added_count = 0
        for file_path in full_path.rglob("*"):
            if file_path.is_file():
                # The repository metadata must never be staged as user content.
                if "mini-git" in file_path.parts:
                    continue

                # Store each file as a blob and stage it using a path relative
                # to the repository root.
                content = file_path.read_bytes()
                blob = Blob(content)
                blob_hash = self.store_object(blob)
                rel_path = str(file_path.relative_to(self.path))
                index[rel_path] = blob_hash
                added_count += 1

        self.save_index(index)

        if added_count > 0:
            print(f"Added {added_count} files from directory {path}")
        else:
            print(f"Directory {path} already up to date")

    def add_path(self, path: str) -> None:
        full_path = self.path / path

        if not full_path.exists():
            raise FileNotFoundError(f"Path {path} not found")

        if full_path.is_file():
            self.add_file(path)
        elif full_path.is_dir():
            self.add_directory(path)
        else:
            raise ValueError(f"{path} is neither a file nor a directory")

    def create_tree_from_index(self):
        index = self.load_index()
        if not index:
            # An empty tree still has a real object identity, just like Git's
            # empty tree object.
            tree = Tree()
            return self.store_object(tree)

        # The index is flat: {"src/app.py": "<blob>"}. Commits need a tree
        # hierarchy, so this first groups staged paths into nested dictionaries.
        dirs = {}
        files = {}

        for file_path, blob_hash in index.items():
            parts = file_path.split("/")

            if len(parts) == 1:
                files[parts[0]] = blob_hash
            else:
                dir_name = parts[0]
                if dir_name not in dirs:
                    dirs[dir_name] = {}

                current = dirs[dir_name]
                for part in parts[1:-1]:
                    if part not in current:
                        current[part] = {}

                    current = current[part]

                current[parts[-1]] = blob_hash

        def create_tree_recursive(entries_dict: Dict):
            tree = Tree()

            for name, blob_hash in entries_dict.items():
                if isinstance(blob_hash, str):
                    # File entries point directly to blob objects.
                    tree.add_entry("100644", name, blob_hash)

                if isinstance(blob_hash, dict):
                    # Directory entries point to child tree objects. Children
                    # must be stored first so the parent can reference the hash.
                    subtree_hash = create_tree_recursive(blob_hash)
                    tree.add_entry("40000", name, subtree_hash)

            return self.store_object(tree)

        root_entries = {**files}
        for dir_name, dir_contents in dirs.items():
            root_entries[dir_name] = dir_contents

        return create_tree_recursive(root_entries)

    def get_current_branch(self) -> str:
        if not self.head_file.exists():
            return "master"

        # HEAD usually stores a symbolic ref like "ref: refs/heads/master".
        # The active branch name is the suffix after that prefix.
        head_content = self.head_file.read_text().strip()
        if head_content.startswith("ref: refs/heads/"):
            return head_content[16:]

        return "HEAD"

    def get_branch_commit(self, current_branch: str):
        # A branch is just a file containing the latest commit hash for that
        # branch. Missing file means the branch has no commit yet.
        branch_file = self.heads_dir / current_branch

        if branch_file.exists():
            return branch_file.read_text().strip()

        return None

    def set_branch_commit(self, current_branch: str, commit_hash: str):
        # Moving a branch forward is just overwriting its ref file with the new
        # commit hash.
        branch_file = self.heads_dir / current_branch
        branch_file.write_text(commit_hash + "\n")

    def commit(
        self,
        message: str,
        author: str = "PyGit User <user@pygit.com>",
    ):
        # The commit snapshot is built from the staged index, not directly from
        # whatever happens to be in the working directory.
        tree_hash = self.create_tree_from_index()

        current_branch = self.get_current_branch()
        parent_commit = self.get_branch_commit(current_branch)
        parent_hashes = [parent_commit] if parent_commit else []

        index = self.load_index()
        if not index:
            print("nothing to commit, working tree clean")
            return None

        if parent_commit:
            # Avoid creating duplicate commits when the staged tree is identical
            # to the current branch tip's tree.
            parent_git_commit_obj = self.load_object(parent_commit)
            parent_commit_data = Commit.from_content(parent_git_commit_obj.content)
            if tree_hash == parent_commit_data.tree_hash:
                print("nothing to commit, working tree clean")
                return None

        commit = Commit(
            tree_hash=tree_hash,
            parent_hashes=parent_hashes,
            author=author,
            committer=author,
            message=message,
        )
        commit_hash = self.store_object(commit)

        # The new commit becomes the current branch tip. Clearing the index keeps
        # this simplified implementation's next commit explicit.
        self.set_branch_commit(current_branch, commit_hash)
        self.save_index({})
        print(f"Created commit {commit_hash} on branch {current_branch}")
        return commit_hash

    def get_files_from_tree_recursive(
        self,
        tree_hash: str,
        prefix: str = "",
    ):
        # Used by checkout to know which files belonged to the previous branch
        # and may need to be removed before restoring the target branch.
        files = set()
        try:
            tree_obj = self.load_object(tree_hash)
            tree = Tree.from_content(tree_obj.content)
            for mode, name, obj_hash in tree.entries:
                full_name = f"{prefix}{name}"
                if mode.startswith("100"):
                    files.add(full_name)
                elif mode.startswith("400"):
                    subtree_files = self.get_files_from_tree_recursive(
                        obj_hash, f"{full_name}/"
                    )
                    files.update(subtree_files)
        except Exception as e:
            print(f"Warning: Could not read tree {tree_hash}: {e}")

        return files

    def checkout(self, branch: str, create_branch: bool):
        # Checkout has two separate jobs:
        # 1. change HEAD to point at the target branch
        # 2. make the working directory match the target branch snapshot
        previous_branch = self.get_current_branch()
        files_to_clear = set()
        try:
            previous_commit_hash = self.get_branch_commit(previous_branch)
            if previous_commit_hash:
                prev_commit_object = self.load_object(previous_commit_hash)
                prev_commit = Commit.from_content(prev_commit_object.content)
                if prev_commit.tree_hash:
                    files_to_clear = self.get_files_from_tree_recursive(
                        prev_commit.tree_hash
                    )
        except Exception:
            files_to_clear = set()

        branch_file = self.heads_dir / branch
        if not branch_file.exists():
            if create_branch:
                # New branches start at the previous branch's current commit.
                if previous_commit_hash:
                    self.set_branch_commit(branch, previous_commit_hash)
                    print(f"Created new branch {branch}")
                else:
                    print("No commits yet, cannot create a branch")
                    return
            else:
                print(f"Branch '{branch}' not found.")
                print(
                    "Use 'python3 main.py checkout -b {branch}' to create and switch to a new branch."
                )
                return
        self.head_file.write_text(f"ref: refs/heads/{branch}\n")

        self.restore_working_directory(branch, files_to_clear)
        print(f"Switched to branch {branch}")

    def restore_tree(self, tree_hash: str, path: Path):
        # Materialize a tree object back into real files and directories.
        tree_obj = self.load_object(tree_hash)
        tree = Tree.from_content(tree_obj.content)
        for mode, name, obj_hash in tree.entries:
            file_path = path / name
            if mode.startswith("100"):
                # File entry: load the blob and write its bytes to disk.
                blob_obj = self.load_object(obj_hash)
                blob = Blob(blob_obj.content)
                file_path.write_bytes(blob.content)
            elif mode.startswith("400"):
                # Directory entry: create the folder, then recursively restore
                # the subtree inside it.
                file_path.mkdir(exist_ok=True)
                self.restore_tree(obj_hash, file_path)

    def restore_working_directory(
        self,
        branch: str,
        files_to_clear: set[str],
    ):
        target_commit_hash = self.get_branch_commit(branch)
        if not target_commit_hash:
            return

        # Remove files tracked by the previous branch so stale files do not
        # remain when the target branch does not contain them.
        for rel_path in sorted(files_to_clear):
            file_path = self.path / rel_path
            try:
                if file_path.is_file():
                    file_path.unlink()
            except Exception:
                pass

        target_commit_obj = self.load_object(target_commit_hash)
        target_commit = Commit.from_content(target_commit_obj.content)

        # The target commit's root tree is enough to recreate the tracked
        # working directory for that branch.
        if target_commit.tree_hash:
            self.restore_tree(target_commit.tree_hash, self.path)

        self.save_index({})

    def branch(self, branch_name: str, delete: bool = False):
        if delete and branch_name:
            # Deleting a branch removes the name/ref, not the underlying commit
            # objects from the object database.
            branch_file = self.heads_dir / branch_name
            if branch_file.exists():
                branch_file.unlink()
                print(f"Deleted branch {branch_name}")
            else:
                print(f"Branch {branch_name} not found")

            return

        current_branch = self.get_current_branch()
        if branch_name:
            # Creating a branch copies the current commit pointer into a new ref.
            current_commit = self.get_branch_commit(current_branch)
            if current_commit:
                self.set_branch_commit(branch_name, current_commit)
                print(f"Created branch {branch_name}")
            else:
                print(f"No commits yet, cannot create a new branch")
        else:
            # Listing branches means listing ref files and marking the branch
            # currently selected by HEAD.
            branches = []
            for branch_file in self.heads_dir.iterdir():
                if branch_file.is_file() and not branch_file.name.startswith("."):
                    branches.append(branch_file.name)

            for branch in sorted(branches):
                current_marker = "* " if branch == current_branch else "  "
                print(f"{current_marker}{branch}")

    def log(self, max_count: int = 10):
        # History is not stored as a separate log file. Start at the current
        # branch tip and follow parent pointers backward.
        current_branch = self.get_current_branch()
        commit_hash = self.get_branch_commit(current_branch)

        if not commit_hash:
            print("No commits yet!")
            return

        count = 0
        while commit_hash and count < max_count:
            commit_obj = self.load_object(commit_hash)
            commit = Commit.from_content(commit_obj.content)

            print(f"commit {commit_hash}")
            print(f"Author: {commit.author}")
            print(f"Date: {time.ctime(commit.timestamp)}")
            print(f"\n    {commit.message}\n")

            commit_hash = commit.parent_hashes[0] if commit.parent_hashes else None
            count += 1

    def build_index_from_tree(self, tree_hash: str, prefix: str = ""):
        # Convert a hierarchical committed tree back into a flat path->hash map.
        # Status uses this to compare the last commit against the index and
        # working directory.
        index = {}
        try:
            tree_obj = self.load_object(tree_hash)
            tree = Tree.from_content(tree_obj.content)
            for mode, name, obj_hash in tree.entries:
                full_name = f"{prefix}{name}"
                if mode.startswith("100"):
                    index[full_name] = obj_hash
                elif mode.startswith("400"):
                    subindex = self.build_index_from_tree(obj_hash, f"{full_name}/")

                    index.update(subindex)
        except Exception as e:
            print(f"Warning: Could not read tree {tree_hash}: {e}")

        return index

    def get_all_files(self) -> List[Path]:
        files = []

        for item in self.path.rglob("*"):
            # Status should inspect user files only, not Git internals.
            if "mini-git" in item.parts:
                continue

            if item.is_file():
                files.append(item)

        return files

    def status(self):
        # Status compares three states:
        # 1. last committed tree
        # 2. staging index
        # 3. current working directory
        current_branch = self.get_current_branch()
        print(f"On branch {current_branch}")
        index = self.load_index()
        current_commit_hash = self.get_branch_commit(current_branch)

        last_index_files = {}
        if current_commit_hash:
            try:
                # Rebuild the latest commit snapshot into the same flat shape
                # as the index: path -> blob hash.
                commit_obj = self.load_object(current_commit_hash)
                commit = Commit.from_content(commit_obj.content)
                if commit.tree_hash:
                    last_index_files = self.build_index_from_tree(commit.tree_hash)
            except:
                last_index_files = {}

        working_files = {}
        for item in self.get_all_files():
            rel_path = str(item.relative_to(self.path))

            try:
                # Hash current working files as blobs so they can be compared
                # against stored blob hashes.
                content = item.read_bytes()
                blob = Blob(content)
                working_files[rel_path] = blob.hash()
            except:
                continue

        staged_files = []
        unstaged_files = []
        untracked_files = []
        deleted_files = []

        for file_path in set(index.keys()) | set(last_index_files.keys()):
            index_hash = index.get(file_path)
            last_index_hash = last_index_files.get(file_path)

            # Index differs from last commit => staged change.
            if index_hash and not last_index_hash:
                staged_files.append(("new file", file_path))
            elif index_hash and last_index_hash and index_hash != last_index_hash:
                staged_files.append(("modified", file_path))

        if staged_files:
            print("\nChanges to be committed:")
            for stage_status, file_path in sorted(staged_files):
                print(f"   {stage_status}: {file_path}")

        for file_path in working_files:
            if file_path in index:
                # Working file differs from staged blob => modified after add.
                if working_files[file_path] != index[file_path]:
                    unstaged_files.append(file_path)

        if unstaged_files:
            print("\nChanges not staged for commit:")
            for file_path in sorted(unstaged_files):
                print(f"   modified: {file_path}")

        for file_path in working_files:
            # Present on disk but absent from both index and last commit.
            if file_path not in index and file_path not in last_index_files:
                untracked_files.append(file_path)

        if untracked_files:
            print("\nUntracked files:")
            for file_path in sorted(untracked_files):
                print(f"   {file_path}")

        for file_path in index:
            # Simplified deletion detection: staged/indexed path is missing from
            # the working directory.
            if file_path not in working_files:
                deleted_files.append(file_path)

        if deleted_files:
            print("\nDeleted files:")
            for file_path in sorted(deleted_files):
                print(f"   deleted: {file_path}")

        if (
            not staged_files
            and not unstaged_files
            and not deleted_files
            and not untracked_files
        ):
            print("\nnothing to commit, working tree clean")
