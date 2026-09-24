from __future__ import annotations

import hashlib
import time
from typing import List, Tuple
import zlib


# Git stores durable data as typed "objects". This base class contains the
# behavior that every object type shares: hash identity, serialization, and
# deserialization from the compressed object database.
class GitObject:
    def __init__(self, obj_type: str, content: bytes):
        self.type = obj_type
        self.content = content

    def hash(self) -> str:
        # The object id is based on the same conceptual payload Git uses:
        # "<type> <size>\0<content>". Including the type prevents a blob and a
        # commit with the same raw bytes from receiving the same identity.
        header = f"{self.type} {len(self.content)}\0".encode()
        return hashlib.sha1(header + self.content).hexdigest()

    def serialize(self) -> bytes:
        # Objects are stored compressed on disk. The hash is computed from the
        # uncompressed payload, while the file content is the compressed payload.
        header = f"{self.type} {len(self.content)}\0".encode()
        return zlib.compress(header + self.content)

    @classmethod
    def deserialize(cls, data: bytes) -> GitObject:
        # Stored object files contain compressed bytes. Loading reverses that
        # process: decompress, split the header at the null byte, then keep the
        # object type and raw content.
        decompressed = zlib.decompress(data)
        null_idx = decompressed.find(b"\0")
        header = decompressed[:null_idx].decode()
        content = decompressed[null_idx + 1 :]

        obj_type, _ = header.split(" ")

        return cls(obj_type, content)


# A blob is only file content. It intentionally does not store the filename;
# names and directory placement are represented by Tree entries.
class Blob(GitObject):
    def __init__(self, content: bytes):
        super().__init__("blob", content)


# A tree represents one directory level. Each entry records:
#   mode: "100644" for file or "40000" for directory
#   name: filename or directory name
#   obj_hash: blob hash for files, tree hash for directories
class Tree(GitObject):
    def __init__(self, entries: List[Tuple[str, str, str]] = None):
        self.entries = entries or []
        content = self._serialize_entries()
        super().__init__("tree", content)

    def _serialize_entries(self) -> bytes:
        content = b""
        # Sorting makes tree serialization deterministic. The same directory
        # contents should always produce the same tree hash.
        for mode, name, obj_hash in sorted(self.entries):
            # Tree objects store hashes as raw 20-byte values, not 40-character
            # hex strings. This mirrors Git's compact tree entry encoding.
            content += f"{mode} {name}\0".encode()
            content += bytes.fromhex(obj_hash)

        return content

    def add_entry(self, mode: str, name: str, obj_hash: str):
        # Tree content is derived from entries, so changing entries means the
        # serialized bytes must be regenerated before hashing or storing.
        self.entries.append((mode, name, obj_hash))
        self.content = self._serialize_entries()

    @classmethod
    def from_content(cls, content: bytes) -> Tree:
        tree = cls()
        i = 0

        while i < len(content):
            null_idx = content.find(b"\0", i)
            if null_idx == -1:
                break

            # Each tree entry is: "<mode> <name>\0<20-byte object hash>".
            # After the null byte, the next 20 bytes are the referenced object's
            # SHA-1 hash in raw binary form.
            mode_name = content[i:null_idx].decode()
            mode, name = mode_name.split(" ", 1)
            obj_hash = content[null_idx + 1 : null_idx + 21].hex()
            tree.entries.append((mode, name, obj_hash))

            i = null_idx + 21

        return tree


# A commit records one repository snapshot. It points to a root tree, links to
# parent commits, and stores human-facing metadata such as author and message.
class Commit(GitObject):
    def __init__(
        self,
        tree_hash: str,
        parent_hashes: List[str],
        author: str,
        committer: str,
        message: str,
        timestamp: int = None,
    ):
        self.tree_hash = tree_hash
        self.parent_hashes = parent_hashes
        self.author = author
        self.committer = committer
        self.message = message
        # If no timestamp is supplied, use the current Unix time. This keeps
        # commit creation simple while still allowing tests to pass a timestamp.
        self.timestamp = timestamp or int(time.time())

        content = self._serialize_commit()
        super().__init__("commit", content)

    def _serialize_commit(self):
        # Commit content is line-oriented text. The blank line separates headers
        # from the commit message, matching the shape of real Git commit data.
        lines = [f"tree {self.tree_hash}"]
        for parent in self.parent_hashes:
            lines.append(f"parent {parent}")

        lines.append(f"author {self.author} {self.timestamp} +0000")
        lines.append(f"committer {self.committer} {self.timestamp} +0000")
        lines.append("")
        lines.append(self.message)

        return "\n".join(lines).encode()

    @classmethod
    def from_content(cls, content: bytes) -> Commit:
        # Parsing reverses _serialize_commit: read header lines until the blank
        # line, then treat everything after it as the message.
        lines = content.decode().split("\n")
        tree_hash = None
        parent_hashes = []
        author = None
        committer = None
        message_start = 0

        for i, line in enumerate(lines):
            if line.startswith("tree "):
                tree_hash = line[5:]
            elif line.startswith("parent "):
                parent_hashes.append(line[7:])
            elif line.startswith("author "):
                author_parts = line[7:].rsplit(" ", 2)
                author = author_parts[0]
                timestamp = int(author_parts[1])
            elif line.startswith("committer "):
                committer_parts = line[10:].rsplit(" ", 2)
                committer = committer_parts[0]
            elif line == "":
                message_start = i + 1
                break

        message = "\n".join(lines[message_start:])
        commit = cls(tree_hash, parent_hashes, author, committer, message, timestamp)
        return commit
