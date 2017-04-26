#!/usr/bin/env python3

import io
import logging
import os
import urllib

from gi.repository import Gio
from gi.repository import GLib

logger = logging.getLogger(__name__)


class GioFileAdapter(io.RawIOBase):
    def __init__(self, gfile, mode='r'):
        super().__init__()
        self.gfile = gfile
        self.mode = mode

        if 'w' in mode:
            self.size = 0
        else:
            try:
                fi = gfile.query_info(
                    Gio.FILE_ATTRIBUTE_STANDARD_SIZE,
                    Gio.FileQueryInfoFlags.NONE
                )
                self.size = fi.get_size()
            except GLib.GError as exc:
                raise IOError(exc)

        self.gfd = None
        self.gin = None
        self.gout = None

        if 'r' in mode:
            self.gin = self.gfd = gfile.read()
        elif 'w' in mode or 'a' in mode:
            if gfile.query_exists():
                self.gfd = gfile.open_readwrite()
            else:
                self.gfd = gfile.create_readwrite()
            self.gin = self.gfd.get_input_stream()
            self.gout = self.gfd.get_output_stream()

        if 'a' in mode:
            self.seek(0, whence=os.SEEK_END)

    def readable(self):
        return True

    def writable(self):
        return 'w' in self.mode

    def read(self, size=-1):
        if not self.readable():
            raise OSError("File is not readable")
        if size < 0:
            size = self.size
        return self.gin.read_bytes(size).get_data()

    def readall(self):
        return self.read(-1)

    def readinto(self, b):
        raise OSError("readinto() not supported on Gio.File objects")

    def readline(self, size=-1):
        raise OSError("readline() not supported on Gio.File objects")

    def readlines(self, hint=-1):
        return [(x + b"\n") for x in self.readall().split(b"\n")]

    def seek(self, offset, whence=os.SEEK_SET):
        whence = {
            os.SEEK_CUR: GLib.SeekType.CUR,
            os.SEEK_END: GLib.SeekType.END,
            os.SEEK_SET: GLib.SeekType.SET,
        }[whence]
        self.gfd.seek(offset, whence)

    def seekable(self):
        return True

    def tell(self):
        return self.gin.tell()

    def flush(self):
        pass

    def truncate(self, size=None):
        if size is None:
            size = self.tell()
        self.gfd.truncate(size)

    def fileno(self):
        raise io.UnsupportedOperation("fileno() called on Gio.File object")

    def isatty(self):
        return False

    def write(self, b):
        res = self.gout.write_all(b)
        if not res[0]:
            raise OSError("write_all() failed on {}: {}".format(
                self.gfile.get_uri(), res)
            )
        return res[1]

    def writelines(self, lines):
        self.write(b"".join(lines))

    def close(self):
        self.flush()
        super().close()
        if self.gin:
            self.gin.close()
        if self.gout:
            self.gout.close()
        if self.gfd is not self.gin:
            self.gfd.close()

    def __enter__(self):
        return self

    def __exit__(self, type, value, traceback):
        self.close()


class GioUTF8FileAdapter(io.RawIOBase):
    def __init__(self, raw):
        super().__init__()
        self.raw = raw

    def readable(self):
        return self.raw.readable()

    def writable(self):
        return self.raw.writable()

    def read(self, *args, **kwargs):
        r = self.raw.read(*args, **kwargs)
        return r.decode("utf-8")

    def readall(self, *args, **kwargs):
        r = self.raw.readall(*args, **kwargs)
        return r.decode("utf-8")

    def readinto(self, *args, **kwargs):
        r = self.raw.readinto(*args, **kwargs)
        return r.decode("utf-8")

    def readlines(self, hint=-1):
        lines = [(x + "\n") for x in self.readall().split(os.linesep)]
        if lines[-1] == "\n":
            return lines[:-1]
        return lines

    def readline(self, hint=-1):
        raise OSError("readline() not supported on Gio.File objects")

    def seek(self, *args, **kwargs):
        return self.raw.seek(*args, **kwargs)

    def seekable(self, seekable):
        return self.raw.seekable()

    @property
    def closed(self):
        return self.raw.closed

    def tell(self):
        # XXX(Jflesch): wrong ...
        return self.raw.tell()

    def flush(self):
        return self.raw.flush()

    def truncate(self, *args, **kwargs):
        # XXX(Jflesch): wrong ...
        return self.raw.truncate(*args, **kwargs)

    def fileno(self):
        return self.raw.fileno()

    def isatty(self):
        return self.raw.isatty()

    def write(self, b):
        b = b.encode("utf-8")
        return self.raw.write(b)

    def writelines(self, lines):
        lines = [
            (line + os.linesep).encode("utf-8")
            for line in lines
        ]
        return self.raw.writelines(lines)

    def close(self):
        self.raw.close()

    def __enter__(self):
        return self

    def __exit__(self, type, value, traceback):
        self.close()


class GioFileSystem(object):
    def __init__(self):
        pass

    def safe(self, uri):
        if "://" not in uri:
            # assume local path
            uri = "file://" + urllib.parse.quote(uri)
        return uri

    def unsafe(self, uri):
        if "://" not in uri:
            return uri
        if not uri.startswith("file://"):
            raise Exception("TARGET URI SHOULD BE A LOCAL FILE")
        uri = uri[len("file://"):]
        uri = urllib.parse.unquote(uri)
        return uri

    def open(self, uri, mode='rb'):
        try:
            raw = GioFileAdapter(Gio.File.new_for_uri(uri), mode)
            if 'b' in mode:
                return raw
            return GioUTF8FileAdapter(raw)
        except GLib.GError as exc:
            raise IOError(exc)

    def join(self, base, url):
        if not base.endswith("/"):
            base += "/"
        return urllib.parse.urljoin(base, url)

    def basename(self, url):
        url = urllib.parse.urlparse(url)
        basename = os.path.basename(url.path)
        # Base name can be safely unquoted
        return urllib.parse.unquote(basename)

    def dirname(self, url):
        # dir name should not be unquoted. It could mess up the URI
        return os.path.dirname(url)

    def exists(self, url):
        try:
            f = Gio.File.new_for_uri(url)
            return f.query_exists()
        except GLib.GError as exc:
            raise IOError(exc)

    def listdir(self, url):
        try:
            f = Gio.File.new_for_uri(url)
            children = f.enumerate_children(
                Gio.FILE_ATTRIBUTE_STANDARD_NAME, Gio.FileQueryInfoFlags.NONE,
                None
            )
            for child in children:
                child = f.get_child(child.get_name())
                yield child.get_uri()
        except GLib.GError as exc:
            raise IOError(exc)

    def rename(self, old_url, new_url):
        try:
            old = Gio.File.new_for_uri(old_url)
            new = Gio.File.new_for_uri(new_url)
            assert(not old.equal(new))
            old.move(new, Gio.FileCopyFlags.NONE)
        except GLib.GError as exc:
            raise IOError(exc)

    def unlink(self, url):
        try:
            f = Gio.File.new_for_uri(url)
            f.delete()
        except GLib.GError as exc:
            raise IOError(exc)

    def getmtime(self, url):
        try:
            f = Gio.File.new_for_uri(url)
            fi = f.query_info(
                Gio.FILE_ATTRIBUTE_TIME_CHANGED, Gio.FileQueryInfoFlags.NONE
            )
            return fi.get_attribute_uint64(Gio.FILE_ATTRIBUTE_TIME_CHANGED)
        except GLib.GError as exc:
            raise IOError(exc)

    def getsize(self, url):
        try:
            f = Gio.File.new_for_uri(url)
            fi = f.query_info(
                Gio.FILE_ATTRIBUTE_STANDARD_SIZE, Gio.FileQueryInfoFlags.NONE
            )
            return fi.get_attribute_uint64(Gio.FILE_ATTRIBUTE_STANDARD_SIZE)
        except GLib.GError as exc:
            raise IOError(exc)

    def isdir(self, url):
        try:
            f = Gio.File.new_for_uri(url)
            fi = f.query_info(
                Gio.FILE_ATTRIBUTE_STANDARD_TYPE, Gio.FileQueryInfoFlags.NONE
            )
            return fi.get_file_type() == Gio.FileType.DIRECTORY
        except GLib.GError as exc:
            raise IOError(exc)

    def copy(self, old_url, new_url):
        try:
            old = Gio.File.new_for_uri(url)
            new = Gio.File.new_for_uri(url)
            old.copy(new, Gio.FileCopyFlags.ALL_METADATA)
        except GLib.GError as exc:
            raise IOError(exc)
