import asyncio
from familiar.transport import FakeTransport, NullTransport, StdoutTransport


def test_fake_records():
    t = FakeTransport()
    asyncio.run(t.send(b"hi\n"))
    assert t.sent == [b"hi\n"]


def test_stdout_prints(capsys):
    t = StdoutTransport()
    asyncio.run(t.send(b'{"a":1}\n'))
    assert '{"a":1}' in capsys.readouterr().out


def test_null_transport_send_is_noop():
    asyncio.run(NullTransport().send(b"anything"))   # no error, no output
