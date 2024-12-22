"""
Abstract base classes for rich path objects.

This module is published as a PyPI package called "pathlib-abc".

This module is also a *PRIVATE* part of the Python standard library, where
it's developed alongside pathlib. If it finds success and maturity as a PyPI
package, it could become a public part of the standard library.

Two base classes are defined here -- PurePathBase and PathBase -- that
resemble pathlib's PurePath and Path respectively.
"""

import functools
import operator
import posixpath
from errno import EINVAL
from glob import _GlobberBase, _no_recurse_symlinks
from stat import S_ISDIR, S_ISLNK, S_ISREG
from pathlib._os import copyfileobj


@functools.cache
def _is_case_sensitive(parser):
    return parser.normcase('Aa') == 'Aa'


def _explode_path(path):
    """
    Split the path into a 2-tuple (anchor, parts), where *anchor* is the
    uppermost parent of the path (equivalent to path.parents[-1]), and
    *parts* is a reversed list of parts following the anchor.
    """
    split = path.parser.split
    path = str(path)
    parent, name = split(path)
    names = []
    while path != parent:
        names.append(name)
        path = parent
        parent, name = split(path)
    return path, names


class PathGlobber(_GlobberBase):
    """
    Class providing shell-style globbing for path objects.
    """

    lexists = operator.methodcaller('exists', follow_symlinks=False)
    add_slash = operator.methodcaller('joinpath', '')
    scandir = operator.methodcaller('_scandir')

    @staticmethod
    def concat_path(path, text):
        """Appends text to the given path."""
        return path.with_segments(str(path) + text)


class CopyWorker:
    """
    Class that implements copying between path objects. An instance of this
    class is available from the PathBase.copy property; it's made callable so
    that PathBase.copy() can be treated as a method.

    The target path's CopyWorker drives the process from its _create() method.
    Files and directories are exchanged by calling methods on the source and
    target paths, and metadata is exchanged by calling
    source.copy._read_metadata() and target.copy._write_metadata().
    """
    __slots__ = ('_path',)

    def __init__(self, path):
        self._path = path

    def __call__(self, target, follow_symlinks=True, dirs_exist_ok=False,
             preserve_metadata=False):
        """
        Recursively copy this file or directory tree to the given destination.
        """
        if not isinstance(target, PathBase):
            target = self._path.with_segments(target)

        # Delegate to the target path's CopyWorker object.
        return target.copy._create(self._path, follow_symlinks, dirs_exist_ok, preserve_metadata)

    _readable_metakeys = frozenset()

    def _read_metadata(self, metakeys, *, follow_symlinks=True):
        """
        Returns path metadata as a dict with string keys.
        """
        raise NotImplementedError

    _writable_metakeys = frozenset()

    def _write_metadata(self, metadata, *, follow_symlinks=True):
        """
        Sets path metadata from the given dict with string keys.
        """
        raise NotImplementedError

    def _create(self, source, follow_symlinks, dirs_exist_ok, preserve_metadata):
        self._ensure_distinct_path(source)
        if preserve_metadata:
            metakeys = self._writable_metakeys & source.copy._readable_metakeys
        else:
            metakeys = None
        if not follow_symlinks and source.is_symlink():
            self._create_symlink(source, metakeys)
        elif source.is_dir():
            self._create_dir(source, metakeys, follow_symlinks, dirs_exist_ok)
        else:
            self._create_file(source, metakeys)
        return self._path

    def _create_dir(self, source, metakeys, follow_symlinks, dirs_exist_ok):
        """Copy the given directory to our path."""
        children = list(source.iterdir())
        self._path.mkdir(exist_ok=dirs_exist_ok)
        for src in children:
            dst = self._path.joinpath(src.name)
            if not follow_symlinks and src.is_symlink():
                dst.copy._create_symlink(src, metakeys)
            elif src.is_dir():
                dst.copy._create_dir(src, metakeys, follow_symlinks, dirs_exist_ok)
            else:
                dst.copy._create_file(src, metakeys)
        if metakeys:
            metadata = source.copy._read_metadata(metakeys)
            if metadata:
                self._write_metadata(metadata)

    def _create_file(self, source, metakeys):
        """Copy the given file to our path."""
        self._ensure_different_file(source)
        with source.open('rb') as source_f:
            try:
                with self._path.open('wb') as target_f:
                    copyfileobj(source_f, target_f)
            except IsADirectoryError as e:
                if not self._path.exists():
                    # Raise a less confusing exception.
                    raise FileNotFoundError(
                        f'Directory does not exist: {self._path}') from e
                raise
        if metakeys:
            metadata = source.copy._read_metadata(metakeys)
            if metadata:
                self._write_metadata(metadata)

    def _create_symlink(self, source, metakeys):
        """Copy the given symbolic link to our path."""
        self._path.symlink_to(source.readlink())
        if metakeys:
            metadata = source.copy._read_metadata(metakeys, follow_symlinks=False)
            if metadata:
                self._write_metadata(metadata, follow_symlinks=False)

    def _ensure_different_file(self, source):
        """
        Raise OSError(EINVAL) if both paths refer to the same file.
        """
        pass

    def _ensure_distinct_path(self, source):
        """
        Raise OSError(EINVAL) if the other path is within this path.
        """
        # Note: there is no straightforward, foolproof algorithm to determine
        # if one directory is within another (a particularly perverse example
        # would be a single network share mounted in one location via NFS, and
        # in another location via CIFS), so we simply checks whether the
        # other path is lexically equal to, or within, this path.
        if source == self._path:
            err = OSError(EINVAL, "Source and target are the same path")
        elif source in self._path.parents:
            err = OSError(EINVAL, "Source path is a parent of target path")
        else:
            return
        err.filename = str(source)
        err.filename2 = str(self._path)
        raise err


class PurePathBase:
    """Base class for pure path objects.

    This class *does not* provide several magic methods that are defined in
    its subclass PurePath. They are: __init__, __fspath__, __bytes__,
    __reduce__, __hash__, __eq__, __lt__, __le__, __gt__, __ge__.
    """

    __slots__ = ()
    parser = posixpath

    def with_segments(self, *pathsegments):
        """Construct a new path object from any number of path-like objects.
        Subclasses may override this method to customize how new path objects
        are created from methods like `iterdir()`.
        """
        raise NotImplementedError

    def __str__(self):
        """Return the string representation of the path, suitable for
        passing to system calls."""
        raise NotImplementedError

    def as_posix(self):
        """Return the string representation of the path with forward (/)
        slashes."""
        return str(self).replace(self.parser.sep, '/')

    @property
    def drive(self):
        """The drive prefix (letter or UNC path), if any."""
        return self.parser.splitdrive(self.anchor)[0]

    @property
    def root(self):
        """The root of the path, if any."""
        return self.parser.splitdrive(self.anchor)[1]

    @property
    def anchor(self):
        """The concatenation of the drive and root, or ''."""
        return _explode_path(self)[0]

    @property
    def name(self):
        """The final path component, if any."""
        return self.parser.split(str(self))[1]

    @property
    def suffix(self):
        """
        The final component's last suffix, if any.

        This includes the leading period. For example: '.txt'
        """
        return self.parser.splitext(self.name)[1]

    @property
    def suffixes(self):
        """
        A list of the final component's suffixes, if any.

        These include the leading periods. For example: ['.tar', '.gz']
        """
        split = self.parser.splitext
        stem, suffix = split(self.name)
        suffixes = []
        while suffix:
            suffixes.append(suffix)
            stem, suffix = split(stem)
        return suffixes[::-1]

    @property
    def stem(self):
        """The final path component, minus its last suffix."""
        return self.parser.splitext(self.name)[0]

    def with_name(self, name):
        """Return a new path with the file name changed."""
        split = self.parser.split
        if split(name)[0]:
            raise ValueError(f"Invalid name {name!r}")
        return self.with_segments(split(str(self))[0], name)

    def with_stem(self, stem):
        """Return a new path with the stem changed."""
        suffix = self.suffix
        if not suffix:
            return self.with_name(stem)
        elif not stem:
            # If the suffix is non-empty, we can't make the stem empty.
            raise ValueError(f"{self!r} has a non-empty suffix")
        else:
            return self.with_name(stem + suffix)

    def with_suffix(self, suffix):
        """Return a new path with the file suffix changed.  If the path
        has no suffix, add given suffix.  If the given suffix is an empty
        string, remove the suffix from the path.
        """
        stem = self.stem
        if not stem:
            # If the stem is empty, we can't make the suffix non-empty.
            raise ValueError(f"{self!r} has an empty name")
        elif suffix and not suffix.startswith('.'):
            raise ValueError(f"Invalid suffix {suffix!r}")
        else:
            return self.with_name(stem + suffix)

    def relative_to(self, other, *, walk_up=False):
        """Return the relative path to another path identified by the passed
        arguments.  If the operation is not possible (because this is not
        related to the other path), raise ValueError.

        The *walk_up* parameter controls whether `..` may be used to resolve
        the path.
        """
        if not isinstance(other, PurePathBase):
            other = self.with_segments(other)
        anchor0, parts0 = _explode_path(self)
        anchor1, parts1 = _explode_path(other)
        if anchor0 != anchor1:
            raise ValueError(f"{str(self)!r} and {str(other)!r} have different anchors")
        while parts0 and parts1 and parts0[-1] == parts1[-1]:
            parts0.pop()
            parts1.pop()
        for part in parts1:
            if not part or part == '.':
                pass
            elif not walk_up:
                raise ValueError(f"{str(self)!r} is not in the subpath of {str(other)!r}")
            elif part == '..':
                raise ValueError(f"'..' segment in {str(other)!r} cannot be walked")
            else:
                parts0.append('..')
        return self.with_segments(*reversed(parts0))

    def is_relative_to(self, other):
        """Return True if the path is relative to another path or False.
        """
        if not isinstance(other, PurePathBase):
            other = self.with_segments(other)
        anchor0, parts0 = _explode_path(self)
        anchor1, parts1 = _explode_path(other)
        if anchor0 != anchor1:
            return False
        while parts0 and parts1 and parts0[-1] == parts1[-1]:
            parts0.pop()
            parts1.pop()
        for part in parts1:
            if part and part != '.':
                return False
        return True

    @property
    def parts(self):
        """An object providing sequence-like access to the
        components in the filesystem path."""
        anchor, parts = _explode_path(self)
        if anchor:
            parts.append(anchor)
        return tuple(reversed(parts))

    def joinpath(self, *pathsegments):
        """Combine this path with one or several arguments, and return a
        new path representing either a subpath (if all arguments are relative
        paths) or a totally different path (if one of the arguments is
        anchored).
        """
        return self.with_segments(str(self), *pathsegments)

    def __truediv__(self, key):
        try:
            return self.with_segments(str(self), key)
        except TypeError:
            return NotImplemented

    def __rtruediv__(self, key):
        try:
            return self.with_segments(key, str(self))
        except TypeError:
            return NotImplemented

    @property
    def parent(self):
        """The logical parent of the path."""
        path = str(self)
        parent = self.parser.split(path)[0]
        if path != parent:
            return self.with_segments(parent)
        return self

    @property
    def parents(self):
        """A sequence of this path's logical parents."""
        split = self.parser.split
        path = str(self)
        parent = split(path)[0]
        parents = []
        while path != parent:
            parents.append(self.with_segments(parent))
            path = parent
            parent = split(path)[0]
        return tuple(parents)

    def is_absolute(self):
        """True if the path is absolute (has both a root and, if applicable,
        a drive)."""
        return self.parser.isabs(str(self))

    def match(self, path_pattern, *, case_sensitive=None):
        """
        Return True if this path matches the given pattern. If the pattern is
        relative, matching is done from the right; otherwise, the entire path
        is matched. The recursive wildcard '**' is *not* supported by this
        method.
        """
        if not isinstance(path_pattern, PurePathBase):
            path_pattern = self.with_segments(path_pattern)
        if case_sensitive is None:
            case_sensitive = _is_case_sensitive(self.parser)
        sep = path_pattern.parser.sep
        path_parts = self.parts[::-1]
        pattern_parts = path_pattern.parts[::-1]
        if not pattern_parts:
            raise ValueError("empty pattern")
        if len(path_parts) < len(pattern_parts):
            return False
        if len(path_parts) > len(pattern_parts) and path_pattern.anchor:
            return False
        globber = PathGlobber(sep, case_sensitive)
        for path_part, pattern_part in zip(path_parts, pattern_parts):
            match = globber.compile(pattern_part)
            if match(path_part) is None:
                return False
        return True

    def full_match(self, pattern, *, case_sensitive=None):
        """
        Return True if this path matches the given glob-style pattern. The
        pattern is matched against the entire path.
        """
        if not isinstance(pattern, PurePathBase):
            pattern = self.with_segments(pattern)
        if case_sensitive is None:
            case_sensitive = _is_case_sensitive(self.parser)
        globber = PathGlobber(pattern.parser.sep, case_sensitive, recursive=True)
        match = globber.compile(str(pattern))
        return match(str(self)) is not None



class PathBase(PurePathBase):
    """Base class for concrete path objects.

    This class provides dummy implementations for many methods that derived
    classes can override selectively; the default implementations raise
    NotImplementedError. The most basic methods, such as stat() and open(),
    directly raise NotImplementedError; these basic methods are called by
    other methods such as is_dir() and read_text().

    The Path class derives this class to implement local filesystem paths.
    Users may derive their own classes to implement virtual filesystem paths,
    such as paths in archive files or on remote storage systems.
    """
    __slots__ = ()

    def stat(self, *, follow_symlinks=True):
        """
        Return the result of the stat() system call on this path, like
        os.stat() does.
        """
        raise NotImplementedError

    # Convenience functions for querying the stat results

    def exists(self, *, follow_symlinks=True):
        """
        Whether this path exists.

        This method normally follows symlinks; to check whether a symlink exists,
        add the argument follow_symlinks=False.
        """
        try:
            self.stat(follow_symlinks=follow_symlinks)
        except (OSError, ValueError):
            return False
        return True

    def is_dir(self, *, follow_symlinks=True):
        """
        Whether this path is a directory.
        """
        try:
            return S_ISDIR(self.stat(follow_symlinks=follow_symlinks).st_mode)
        except (OSError, ValueError):
            return False

    def is_file(self, *, follow_symlinks=True):
        """
        Whether this path is a regular file (also True for symlinks pointing
        to regular files).
        """
        try:
            return S_ISREG(self.stat(follow_symlinks=follow_symlinks).st_mode)
        except (OSError, ValueError):
            return False

    def is_symlink(self):
        """
        Whether this path is a symbolic link.
        """
        try:
            return S_ISLNK(self.stat(follow_symlinks=False).st_mode)
        except (OSError, ValueError):
            return False

    def open(self, mode='r', buffering=-1, encoding=None,
             errors=None, newline=None):
        """
        Open the file pointed to by this path and return a file object, as
        the built-in open() function does.
        """
        raise NotImplementedError

    def read_bytes(self):
        """
        Open the file in bytes mode, read it, and close the file.
        """
        with self.open(mode='rb', buffering=0) as f:
            return f.read()

    def read_text(self, encoding=None, errors=None, newline=None):
        """
        Open the file in text mode, read it, and close the file.
        """
        with self.open(mode='r', encoding=encoding, errors=errors, newline=newline) as f:
            return f.read()

    def write_bytes(self, data):
        """
        Open the file in bytes mode, write to it, and close the file.
        """
        # type-check for the buffer interface before truncating the file
        view = memoryview(data)
        with self.open(mode='wb') as f:
            return f.write(view)

    def write_text(self, data, encoding=None, errors=None, newline=None):
        """
        Open the file in text mode, write to it, and close the file.
        """
        if not isinstance(data, str):
            raise TypeError('data must be str, not %s' %
                            data.__class__.__name__)
        with self.open(mode='w', encoding=encoding, errors=errors, newline=newline) as f:
            return f.write(data)

    def _scandir(self):
        """Yield os.DirEntry-like objects of the directory contents.

        The children are yielded in arbitrary order, and the
        special entries '.' and '..' are not included.
        """
        import contextlib
        return contextlib.nullcontext(self.iterdir())

    def iterdir(self):
        """Yield path objects of the directory contents.

        The children are yielded in arbitrary order, and the
        special entries '.' and '..' are not included.
        """
        raise NotImplementedError

    def glob(self, pattern, *, case_sensitive=None, recurse_symlinks=True):
        """Iterate over this subtree and yield all existing files (of any
        kind, including directories) matching the given relative pattern.
        """
        if not isinstance(pattern, PurePathBase):
            pattern = self.with_segments(pattern)
        anchor, parts = _explode_path(pattern)
        if anchor:
            raise NotImplementedError("Non-relative patterns are unsupported")
        if case_sensitive is None:
            case_sensitive = _is_case_sensitive(self.parser)
            case_pedantic = False
        elif case_sensitive == _is_case_sensitive(self.parser):
            case_pedantic = False
        else:
            case_pedantic = True
        recursive = True if recurse_symlinks else _no_recurse_symlinks
        globber = PathGlobber(self.parser.sep, case_sensitive, case_pedantic, recursive)
        select = globber.selector(parts)
        return select(self)

    def rglob(self, pattern, *, case_sensitive=None, recurse_symlinks=True):
        """Recursively yield all existing files (of any kind, including
        directories) matching the given relative pattern, anywhere in
        this subtree.
        """
        if not isinstance(pattern, PurePathBase):
            pattern = self.with_segments(pattern)
        pattern = '**' / pattern
        return self.glob(pattern, case_sensitive=case_sensitive, recurse_symlinks=recurse_symlinks)

    def walk(self, top_down=True, on_error=None, follow_symlinks=False):
        """Walk the directory tree from this directory, similar to os.walk()."""
        paths = [self]
        while paths:
            path = paths.pop()
            if isinstance(path, tuple):
                yield path
                continue
            dirnames = []
            filenames = []
            if not top_down:
                paths.append((path, dirnames, filenames))
            try:
                with path._scandir() as entries:
                    for entry in entries:
                        name = entry.name
                        try:
                            if entry.is_dir(follow_symlinks=follow_symlinks):
                                if not top_down:
                                    paths.append(path.joinpath(name))
                                dirnames.append(name)
                            else:
                                filenames.append(name)
                        except OSError:
                            filenames.append(name)
            except OSError as error:
                if on_error is not None:
                    on_error(error)
                if not top_down:
                    while not isinstance(paths.pop(), tuple):
                        pass
                continue
            if top_down:
                yield path, dirnames, filenames
                paths += [path.joinpath(d) for d in reversed(dirnames)]

    def readlink(self):
        """
        Return the path to which the symbolic link points.
        """
        raise NotImplementedError

    def symlink_to(self, target, target_is_directory=False):
        """
        Make this path a symlink pointing to the target path.
        Note the order of arguments (link, target) is the reverse of os.symlink.
        """
        raise NotImplementedError

    def mkdir(self, mode=0o777, parents=False, exist_ok=False):
        """
        Create a new directory at this given path.
        """
        raise NotImplementedError

    copy = property(CopyWorker, doc=CopyWorker.__call__.__doc__)

    def copy_into(self, target_dir, *, follow_symlinks=True,
                  dirs_exist_ok=False, preserve_metadata=False):
        """
        Copy this file or directory tree into the given existing directory.
        """
        name = self.name
        if not name:
            raise ValueError(f"{self!r} has an empty name")
        elif isinstance(target_dir, PathBase):
            target = target_dir / name
        else:
            target = self.with_segments(target_dir, name)
        return self.copy(target, follow_symlinks=follow_symlinks,
                         dirs_exist_ok=dirs_exist_ok,
                         preserve_metadata=preserve_metadata)

    def _delete(self):
        """
        Delete this file or directory (including all sub-directories).
        """
        raise NotImplementedError

    def move(self, target):
        """
        Recursively move this file or directory tree to the given destination.
        """
        target = self.copy(target, follow_symlinks=False, preserve_metadata=True)
        self._delete()
        return target

    def move_into(self, target_dir):
        """
        Move this file or directory tree into the given existing directory.
        """
        name = self.name
        if not name:
            raise ValueError(f"{self!r} has an empty name")
        elif isinstance(target_dir, PathBase):
            target = target_dir / name
        else:
            target = self.with_segments(target_dir, name)
        return self.move(target)
