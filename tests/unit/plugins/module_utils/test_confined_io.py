# Copyright: (c) 2026, Richard Lynch
# GNU General Public License v3.0+ (see https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
#!/usr/bin/env python3
"""confined_io: every primitive against a planted symlink, the owner checks, run_as, and the mutation
proofs that show each protection is load-bearing.

A role that ships this module to a host carries a VENDORED copy under its own files/. The copy may
carry a different module docstring and an SPDX comment, so a copy is compared by CODE: its AST with
the module docstring removed must equal this module's (``code_of``). The copies' own repositories
run that comparison; here the module under test is the canonical one (``CONFINED_IO_SRC`` points
the suite at another copy, which is how a MutationProof swaps in a broken one).
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
# tests/unit/plugins/module_utils -> the collection root
COLLECTION = HERE.parent.parent.parent.parent
CANONICAL = COLLECTION / "plugins" / "module_utils" / "confined_io.py"


def code_of(source: str) -> str:
    """The module's code: its AST with the module docstring dropped (comments are never in it)."""
    tree = ast.parse(source)
    body = tree.body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(getattr(body[0], "value", None), ast.Constant)
    ):
        if isinstance(body[0].value.value, str):
            tree.body = body[1:]
    return ast.dump(tree)


SECRET = "OUTSIDE-SECRET\n"


def load(path: Path, name: str = "confined_io"):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


C = load(Path(os.environ.get("CONFINED_IO_SRC") or CANONICAL))


class Base(unittest.TestCase):
    C = C  # the module under test; MutationProof swaps it

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.t = Path(self._t.name)
        self.root = self.t / "root"
        self.outside = self.t / "outside"
        self.root.mkdir()
        self.outside.mkdir()
        (self.outside / "secret.txt").write_text(SECRET)

    def outside_untouched(self):
        self.assertEqual(sorted(p.name for p in self.outside.iterdir()), ["secret.txt"])
        self.assertEqual((self.outside / "secret.txt").read_text(), SECRET)


class Parts(Base):
    def test_absolute_and_dotdot_refused(self):
        for bad in ("", "/etc/passwd", "../x", "a/../../x", "a/.."):
            with self.assertRaises(self.C.Refused, msg=bad):
                self.C.parts(bad)

    def test_near_miss_ordinary(self):
        self.assertEqual(self.C.parts("a/./b//c"), ["a", "b", "c"])
        self.assertEqual(self.C.parts("..hidden/x"), ["..hidden", "x"])


class Anchor(Base):
    def test_link_anchor_refused(self):
        os.symlink(self.outside, self.t / "link")
        with self.assertRaises(self.C.Refused):
            self.C.open_anchor(self.t / "link")

    def test_owner_and_private(self):
        with self.assertRaises(self.C.Refused):
            self.C.open_anchor(self.root, owner=os.geteuid() + 1)
        os.chmod(self.root, 0o777)
        with self.assertRaises(self.C.Refused):
            self.C.open_anchor(self.root, private=True)
        os.chmod(self.root, 0o755)
        os.close(self.C.open_anchor(self.root, owner=os.geteuid(), private=True))  # near miss

    def test_fd_anchor_is_dup_not_taken(self):
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, fd)
        self.C.write_file(fd, "x.txt", "x")
        os.fstat(fd)  # still open: the caller keeps ownership
        self.assertEqual((self.root / "x.txt").read_text(), "x")


class OpenDir(Base):
    def test_link_at_intermediate_refused(self):
        (self.root / "a").mkdir()
        os.symlink(self.outside, self.root / "a" / "b")
        with self.assertRaises(self.C.Refused):
            self.C.open_dir(self.root, "a/b/c", create=True)
        self.outside_untouched()

    def test_file_at_intermediate_refused(self):
        (self.root / "a").write_text("file")
        with self.assertRaises(self.C.Refused):
            self.C.open_dir(self.root, "a/b", create=True)

    def test_create_mode_and_created_list(self):
        made: list = []
        with self.C.dir_at(self.root, "a/b", create=True, mode=0o750, created=made):
            pass
        self.assertEqual(made, ["a", "a/b"])
        self.assertEqual((self.root / "a" / "b").stat().st_mode & 0o777, 0o750)

    def test_missing_without_create_refused(self):
        with self.assertRaises(self.C.Refused):
            self.C.open_dir(self.root, "nope")

    def test_foreign_owner_refused_near_miss_passes(self):
        (self.root / "a").mkdir()
        with self.assertRaises(self.C.Refused):
            self.C.open_dir(self.root, "a", owners=[os.geteuid() + 1])
        os.close(self.C.open_dir(self.root, "a", owners=[os.geteuid() + 1, os.geteuid()]))

    def test_swap_between_mkdir_and_open_is_refused(self):
        real_mkdir = os.mkdir

        def mkdir_then_swap(name, mode=0o777, *, dir_fd=None):
            real_mkdir(name, mode, dir_fd=dir_fd)
            os.rmdir(name, dir_fd=dir_fd)
            os.symlink(self.outside, name, dir_fd=dir_fd)

        with mock.patch.object(os, "mkdir", mkdir_then_swap):
            with self.assertRaises(self.C.Refused):
                self.C.write_file(self.root, "a/x.txt", "x", create_dirs=True)
        self.outside_untouched()


class OpenFile(Base):
    def test_link_leaf_refused(self):
        os.symlink(self.outside / "secret.txt", self.root / "f")
        with self.assertRaises(self.C.Refused):
            self.C.read_file(self.root, "f")

    def test_link_parent_refused(self):
        os.symlink(self.outside, self.root / "d")
        with self.assertRaises(self.C.Refused):
            self.C.read_file(self.root, "d/secret.txt")

    def test_fifo_and_directory_refused(self):
        os.mkfifo(self.root / "fifo")
        (self.root / "dir").mkdir()
        for name in ("fifo", "dir"):
            with self.assertRaises(self.C.Refused, msg=name):
                self.C.read_file(self.root, name)

    def test_never_creates(self):
        with self.assertRaises(ValueError):
            self.C.open_file(self.root, "f", os.O_WRONLY | os.O_CREAT)

    def test_near_miss_reads_and_rewrites(self):
        (self.root / "d").mkdir()
        (self.root / "d" / "f").write_text("hello")
        self.assertEqual(self.C.read_file(self.root, "d/f"), b"hello")
        fd = self.C.open_file(self.root, "d/f", os.O_RDWR)
        with os.fdopen(fd, "r+b") as fh:
            fh.write(b"HE")
        self.assertEqual((self.root / "d" / "f").read_text(), "HEllo")

    def test_file_owner_check(self):
        (self.root / "f").write_text("x")
        with self.assertRaises(self.C.Refused):
            self.C.read_file(self.root, "f", file_owners=[os.geteuid() + 1])


class WriteFile(Base):
    def test_near_miss_writes_replaces_leaves_no_tmp(self):
        self.C.write_file(self.root, "a/b/f.json", "one", create_dirs=True)
        f = self.root / "a" / "b" / "f.json"
        self.assertEqual(f.read_text(), "one")
        self.assertEqual(f.stat().st_mode & 0o777, 0o644)
        self.C.write_file(self.root, "a/b/f.json", b"two", mode=0o600)
        self.assertEqual(f.read_text(), "two")
        self.assertEqual(f.stat().st_mode & 0o777, 0o600)
        self.assertEqual([p.name for p in f.parent.iterdir()], ["f.json"])

    def test_link_at_leaf_is_replaced_never_written_through(self):
        dest = self.root / "f"
        os.symlink(self.outside / "secret.txt", dest)
        self.C.write_file(self.root, "f", "mine")
        self.outside_untouched()
        self.assertFalse(dest.is_symlink())
        self.assertEqual(dest.read_text(), "mine")

    def test_dangling_link_at_leaf_creates_nothing_outside(self):
        os.symlink(self.outside / "new.txt", self.root / "f")
        self.C.write_file(self.root, "f", "x")
        self.outside_untouched()

    def test_link_at_intermediate_refused(self):
        os.symlink(self.outside, self.root / "a")
        with self.assertRaises(self.C.Refused):
            self.C.write_file(self.root, "a/f", "x", create_dirs=True)
        self.outside_untouched()

    def test_link_anchor_refused(self):
        os.symlink(self.outside, self.t / "linkroot")
        with self.assertRaises(self.C.Refused):
            self.C.write_file(self.t / "linkroot", "f", "x")
        self.outside_untouched()

    def test_directory_at_name_refused(self):
        (self.root / "d").mkdir()
        (self.root / "d" / "keep").write_text("k")
        with self.assertRaises(self.C.Refused):
            self.C.write_file(self.root, "d", "x")
        self.assertEqual((self.root / "d" / "keep").read_text(), "k")
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["d"])  # tmp cleaned

    def test_chown_to_self_and_owners(self):
        ids = (os.geteuid(), os.getegid())
        self.C.write_file(self.root, "d/f", "x", create_dirs=True, chown=ids, dir_chown=ids)
        with self.assertRaises(self.C.Refused):
            self.C.write_file(self.root, "d/g", "x", owners=[os.geteuid() + 1])
        self.assertFalse((self.root / "d" / "g").exists())

    def test_keep_owner_takes_the_replaced_file_or_the_directory(self):
        (self.root / "f").write_text("old")
        old = (self.root / "f").stat()
        with mock.patch.object(os, "fchown") as fchown:
            self.C.write_file(self.root, "f", "new", keep_owner=True)
        self.assertEqual(fchown.call_args.args[1:], (old.st_uid, old.st_gid))
        self.assertEqual((self.root / "f").read_text(), "new")
        # a link at the name is not "the replaced file": its target's owner is never consulted
        os.symlink(self.outside / "secret.txt", self.root / "g")
        with mock.patch.object(os, "fchown") as fchown:
            self.C.write_file(self.root, "g", "x", keep_owner=True)
        d = self.root.stat()
        self.assertEqual(fchown.call_args.args[1:], (d.st_uid, d.st_gid))
        self.outside_untouched()
        with self.assertRaises(ValueError):
            self.C.write_file(self.root, "f", "x", keep_owner=True, chown=(0, 0))


class Unlink(Base):
    def test_link_removed_target_untouched(self):
        os.symlink(self.outside / "secret.txt", self.root / "l")
        self.assertTrue(self.C.unlink(self.root, "l"))
        self.assertFalse(os.path.lexists(self.root / "l"))
        self.outside_untouched()

    def test_link_parent_refused(self):
        os.symlink(self.outside, self.root / "d")
        with self.assertRaises(self.C.Refused):
            self.C.unlink(self.root, "d/secret.txt")
        self.outside_untouched()

    def test_directory_refused_missing_false_file_removed(self):
        (self.root / "d").mkdir()
        with self.assertRaises(self.C.Refused):
            self.C.unlink(self.root, "d")
        self.assertFalse(self.C.unlink(self.root, "nope"))
        (self.root / "f").write_text("x")
        self.assertTrue(self.C.unlink(self.root, "f"))
        self.assertFalse((self.root / "f").exists())


class EnsureDir(Base):
    def test_created_then_idempotent(self):
        ids = (os.geteuid(), os.getegid())
        first = self.C.ensure_dir(self.root, "a/b", chown=ids, owners=[ids[0]], own_leaf=True)
        self.assertEqual(first, {"created": ["a", "a/b"], "repaired": []})
        again = self.C.ensure_dir(self.root, "a/b", chown=ids, owners=[ids[0]], own_leaf=True)
        self.assertEqual(again, {"created": [], "repaired": []})

    def test_link_refused(self):
        os.symlink(self.outside, self.root / "a")
        with self.assertRaises(self.C.Refused):
            self.C.ensure_dir(self.root, "a/b")
        self.outside_untouched()


class RunAs(Base):
    """The id-changing calls are faked (an unprivileged test cannot setresuid); the fork and the
    write are real, which still proves the write happens in a child AFTER the drop."""

    def setUp(self):
        super().setUp()
        self.log = self.t / "drop.log"
        self.ids = {"euid": 0, "egid": 0}
        self.dest = self.root / "w.json"

        def rec(*a):
            with open(self.log, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(a) + "\n")

        def setresgid(r, e, s):
            rec("setresgid", e, os.path.exists(self.dest))
            self.ids["egid"] = e

        def setresuid(r, e, s):
            rec("setresuid", e, os.path.exists(self.dest))
            self.ids["euid"] = e

        for p in (
            mock.patch.object(os, "geteuid", lambda: self.ids["euid"]),
            mock.patch.object(os, "getegid", lambda: self.ids["egid"]),
            mock.patch.object(os, "setgroups", lambda g: rec("setgroups", g)),
            mock.patch.object(os, "setresgid", setresgid, create=True),
            mock.patch.object(os, "setresuid", setresuid, create=True),
        ):
            p.start()
            self.addCleanup(p.stop)

    def calls(self):
        return (
            [json.loads(x) for x in self.log.read_text().splitlines()] if self.log.exists() else []
        )

    def test_write_happens_after_the_drop(self):
        u, g = os.getuid(), os.getgid()
        self.C.run_as(u, g, lambda: self.C.write_file(self.root, "w.json", "as agent"))
        self.assertEqual(self.dest.read_text(), "as agent")
        self.assertEqual(
            self.calls(),
            [["setgroups", []], ["setresgid", g, False], ["setresuid", u, False]],
        )

    def test_failed_drop_runs_nothing(self):
        def refuse(*a):
            raise PermissionError("not permitted")

        with mock.patch.object(os, "setresuid", refuse, create=True):
            with self.assertRaises(self.C.Refused):
                self.C.run_as(1, 1, lambda: self.C.write_file(self.root, "w.json", "x"))
        self.assertFalse(self.dest.exists())

    def test_child_failure_reaches_the_parent(self):
        os.symlink(self.outside, self.root / "d")
        u, g = os.getuid(), os.getgid()
        with self.assertRaises(self.C.Refused) as ctx:
            self.C.run_as(u, g, lambda: self.C.write_file(self.root, "d/x", "x"))
        self.assertIn("not a real directory", str(ctx.exception))
        self.outside_untouched()


# The attack cases a protection must be visible to, keyed by the mutation that removes it.
MUTATIONS = {
    # The walk follows links: every intermediate-link case must go red.
    "DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW": (
        "DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY",
        [
            "OpenDir.test_link_at_intermediate_refused",
            "OpenDir.test_swap_between_mkdir_and_open_is_refused",
            "WriteFile.test_link_at_intermediate_refused",
            "WriteFile.test_link_anchor_refused",
            "Unlink.test_link_parent_refused",
            "EnsureDir.test_link_refused",
        ],
    ),
    # The leaf is opened by name, following a link: the leaf cases must go red.
    "_FILE_FLAGS = os.O_NOFOLLOW": (
        "_FILE_FLAGS = 0",
        ["OpenFile.test_link_leaf_refused"],
    ),
}


class MutationProof(unittest.TestCase):
    def test_each_mutation_turns_its_cases_red(self):
        src = CANONICAL.read_text()
        for needle, (replacement, cases) in MUTATIONS.items():
            self.assertIn(needle, src, f"mutation anchor gone: {needle!r}")
            with tempfile.TemporaryDirectory() as td:
                broken = Path(td) / "confined_io.py"
                broken.write_text(src.replace(needle, replacement, 1))
                mutant = load(broken, "confined_io_mutant")
                for case in cases:
                    cls_name, meth = case.split(".")
                    cls = type(cls_name, (globals()[cls_name],), {"C": mutant})
                    result = unittest.TestResult()
                    cls(meth).run(result)
                    self.assertFalse(
                        result.wasSuccessful(),
                        f"{case} still passes with {needle!r} removed: it cannot see that protection",
                    )


if __name__ == "__main__":
    unittest.main(verbosity=1)
