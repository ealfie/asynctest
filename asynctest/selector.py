# coding: utf-8
"""
Mocking of Selector
-------------------

Mock of :mod:`selectors` and compatible objects performing asynchronous IO.

This module provides classes to mock objects performing IO (files, sockets,
etc). These mocks are compatible with :class:`~asynctest.TestSelector`, which
can simulate the behavior of a selector on the mock objects, or forward actual
work to a real selector.
"""

import selectors
import socket
import ssl

from . import mock


class FileDescriptor(int):
    """
    A subclass of int which allows to identify the virtual file-descriptor of a
    :class:`~asynctest.FileMock`.

    If :class:`~asynctest.FileDescriptor()` without argument, its value will be
    the value of :data:`~FileDescriptor.next_fd`.

    When an object is created, :data:`~FileDescriptor.next_fd` is set to the
    highest value for a :class:`~asynctest.FileDescriptor` object + 1.
    """
    next_fd = 0

    def __new__(cls, *args, **kwargs):
        if not args and not kwargs:
            s = super().__new__(cls, FileDescriptor.next_fd)
        else:
            s = super().__new__(cls, *args, **kwargs)

        FileDescriptor.next_fd = max(FileDescriptor.next_fd + 1, s + 1)

        return s

    def __hash__(self):
        # Return a different hash than the int so we can register both a
        # FileDescriptor object and an int of the same value
        return hash('__FileDescriptor_{}'.format(self))


def fd(fileobj):
    """
    Return the :class:`~asynctest.FileDescriptor` value of ``fileobj``.

    If ``fileobj`` is a :class:`~asynctest.FileDescriptor`, ``fileobj`` is
    returned, else ``fileobj.fileno()``  is returned instead.

    Note that if fileobj is an int, :exc:`ValueError` is raised.

    :raise ValueError: if filobj is not a :class:`~asynctest.FileMock`,
                       a file-like object or
                       a :class:`~asynctest.FileDescriptor`.
    """
    try:
        return fileobj if isinstance(fileobj, FileDescriptor) else fileobj.fileno()
    except Exception:
        raise ValueError


def isfilemock(obj):
    """
    Return ``True`` if the ``obj`` or ``obj.fileno()`` is
    a :class:`asynctest.FileDescriptor`.
    """
    try:
        return (isinstance(obj, FileDescriptor) or
                isinstance(obj.fileno(), FileDescriptor))
    except AttributeError:
        # obj has no attribute fileno()
        return False


class FileMock(mock.Mock):
    """
    Mock a file-like object.

    A FileMock is an intelligent mock which can work with TestSelector to
    simulate IO events during tests.

    .. method:: fileno()

        Return a :class:`~asynctest.FileDescriptor` object.
    """
    def __init__(self, *args, parent=None, **kwargs):
        super().__init__(*args, **kwargs)

        self.fileno.return_value = FileDescriptor()

    def _get_child_mock(self, *args, **kwargs):
        # A FileMock returns a Mock by default, not a FileMock
        return mock.Mock(**kwargs)


class SocketMock(FileMock):
    """
    Mock a socket.

    See :class:`~asynctest.FileMock`.
    """
    def __init__(self, side_effect=None, return_value=mock.DEFAULT,
                 wraps=None, name=None, spec_set=None, parent=None,
                 **kwargs):
        super().__init__(socket.socket, side_effect=side_effect,
                         return_value=return_value, wraps=wraps, name=name,
                         spec_set=spec_set, parent=parent, **kwargs)


class SSLSocketMock(SocketMock):
    """
    Mock a socket wrapped by the :mod:`ssl` module.

    See :class:`~asynctest.FileMock`.

    .. versionadded:: 0.5
    """
    def __init__(self, side_effect=None, return_value=mock.DEFAULT,
                 wraps=None, name=None, spec_set=None, parent=None,
                 **kwargs):
        FileMock.__init__(self, ssl.SSLSocket, side_effect=side_effect,
                          return_value=return_value, wraps=wraps, name=name,
                          spec_set=spec_set, parent=parent, **kwargs)


def _set_event_ready(fileobj, loop, event):
    selector = loop._selector
    fd = selector._fileobj_lookup(fileobj)

    if fd in selector._fd_to_key:
        loop._process_events([(selector._fd_to_key[fd], event)])


def set_read_ready(fileobj, loop):
    """
    Schedule callbacks registered on ``loop`` as if the selector notified that
    data is ready to be read on ``fileobj``.

    :param fileobj: file object or :class:`~asynctest.FileMock` on which the
                    event is mocked.

    :param loop: :class:`asyncio.SelectorEventLoop` watching for events on
                 ``fileobj``.

    ::

        mock = asynctest.SocketMock()
        mock.recv.return_value = b"Data"

        def read_ready(sock):
            print("received:", sock.recv(1024))

        loop.add_reader(mock, read_ready, mock)

        set_read_ready()

        loop.run_forever() # prints received: b"Data"

    .. versionadded:: 0.4
    """
    # since the selector whould notify of events at the begining of the next
    # iteration, we let this iteration finish before actually scheduling the
    # reader (hence the call_soon)
    loop.call_soon_threadsafe(_set_event_ready, fileobj, loop, selectors.EVENT_READ)


def set_write_ready(fileobj, loop):
    """
    Schedule callbacks registered on ``loop`` as if the selector notified that
    data can be written to ``fileobj``.

    :param fileobj: file obkect or  :class:`~asynctest.FileMock` on which th
    event is mocked.

    :param loop: :class:`asyncio.SelectorEventLoop` watching for events on
    ``fileobj``.

    .. versionadded:: 0.4
    """
    loop.call_soon_threadsafe(_set_event_ready, fileobj, loop, selectors.EVENT_WRITE)


class TestSelector(selectors._BaseSelectorImpl):
    """
    A selector which supports IOMock objects.

    It can wrap an actual implementation of a selector, so the selector will
    work both with mocks and real file-like objects.

    A common use case is to patch the selector loop:

    ::

        loop._selector = asynctest.TestSelector(loop._selector)

    :param selector: optional, if provided, this selector will be used to work
                     with real file-like objects.
    """
    def __init__(self, selector=None):
        super().__init__()
        self._selector = selector

    def _fileobj_lookup(self, fileobj):
        if isfilemock(fileobj):
            return fd(fileobj)

        return super()._fileobj_lookup(fileobj)

    def register(self, fileobj, events, data=None):
        """
        Register a file object or a :class:`~asynctest.FileMock`.

        If a real selector object has been supplied to the
        :class:`~asynctest.TestSelector` object and ``fileobj`` is not
        a :class:`~asynctest.FileMock` or a :class:`~asynctest.FileDescriptor`
        returned by :meth:`FileMock.fileno()`, the object will be registered to
        the real selector.

        See :meth:`selectors.BaseSelector.register`.
        """
        if isfilemock(fileobj) or self._selector is None:
            key = super().register(fileobj, events, data)
        else:
            key = self._selector.register(fileobj, events, data)

            if key:
                self._fd_to_key[key.fd] = key

        return key

    def unregister(self, fileobj):
        """
        Unregister a file object or a :class:`~asynctest.FileMock`.

        See :meth:`selectors.BaseSelector.unregister`.
        """
        if isfilemock(fileobj) or self._selector is None:
            key = super().unregister(fileobj)
        else:
            key = self._selector.unregister(fileobj)

            if key and key.fd in self._fd_to_key:
                del self._fd_to_key[key.fd]

        return key

    def modify(self, fileobj, events, data=None):
        """
        Shortcut when calling :meth:`TestSelector.unregister` then
        :meth:`TestSelector.register` to update the registration of a an object
        to the selector.

        See :meth:`selectors.BaseSelector.modify`.
        """
        if isfilemock(fileobj) or self._selector is None:
            key = super().modify(fileobj, events, data)
        else:
            # del the key first because modify() fails if events is incorrect
            fd = self._fileobj_lookup(fileobj)

            if fd in self._fd_to_key:
                del self._fd_to_key[fd]

            key = self._selector.modify(fileobj, events, data)

            if key:
                self._fd_to_key[key.fd] = key

        return key

    def select(self, timeout=None):
        """
        Perfom the selection.

        This method is a no-op if no actual selector has been supplied.

        See :meth:`selectors.BaseSelector.select`.
        """
        if self._selector is None:
            return []

        return self._selector.select(timeout)

    def close(self):
        """
        Close the selector.

        Close the actual selector if supplied, unregister all mocks.

        See :meth:`selectors.BaseSelector.close`.
        """
        if self._selector is not None:
            self._selector.close()

        super().close()
