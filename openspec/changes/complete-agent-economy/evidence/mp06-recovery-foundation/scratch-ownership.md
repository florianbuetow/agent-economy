# Recovery scratch ownership decision

Repeated review exposed a real limitation in the proposed cleanup algorithm: `mkdir` does not
return the directory's descriptor, and `stat` followed by `rmdir(name, dir_fd=parent)` is not an
atomic compare-and-remove operation. Keeping an older descriptor open does not bind deletion of
the current directory entry to that descriptor's inode. Additional stat checks cannot establish
that guarantee against an arbitrary uncooperative process using the same OS account.

Primary evidence: Python exposes path-based `mkdir` and descriptor-relative `rmdir`/`unlink`, with
no expected-inode parameter. POSIX specifies individual directory operations, not a multi-call
transaction. [Python os](https://docs.python.org/3.13/library/os.html#os.rmdir),
[POSIX unlinkat](https://pubs.opengroup.org/onlinepubs/9699919799/functions/unlink.html).
`shutil.rmtree`'s descriptor-based implementation protects against symlink traversal; it does not
promise conditional inode-bound removal of a replaced name.
[Python shutil](https://docs.python.org/3.13/library/shutil.html#shutil.rmtree),
[CPython 3.13.5 implementation](https://github.com/python/cpython/blob/v3.13.5/Lib/shutil.py).

The revised contract uses an effective-user-owned, mode-0700 work root and a persistent exclusive
recovery lease for all public recovery operations. Same-UID participants must cooperate with that
lease; an advisory lock does not confine arbitrary code running under the same identity.
[Apple flock](https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man2/flock.2.html),
[Linux flock](https://man7.org/linux/man-pages/man2/flock.2.html).

Scratch creation records the post-mkdir identity immediately, compares the opened descriptor and
current entry, and refuses unexpected replacement. SQLite and key readers operate through verified
descriptor-derived paths. The engine closes resources but **retains scratch directories and files**
on success and failure. It does not recursively delete or remove named scratch entries. This removes
the cleanup deletion race instead of claiming an unavailable atomic filesystem primitive.

The caller's dedicated work root makes retention explicit and bounds ownership. Its owning disposable
runtime/test lifecycle may dispose of it after recovery ends. Ordinary source data and immutable
backup artifacts are not scratch and retain their existing protections. Artifact audit contract v1
and existing backup bytes remain compatible. Required work-root mode/lease checks and retention
tests are being implemented; this decision alone is not verification or task-5.2 closure.
