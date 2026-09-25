"""Additional Click resource cleanup checks for the synthetic CLI control."""

import sys
from pathlib import Path


source = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(source / "src"))
import click  # noqa: E402


class Boom(Exception):
    pass


class CleanupError(Exception):
    pass


class Resource:
    def __init__(self, name, events, *, suppress=False, raises=False):
        self.name = name
        self.events = events
        self.suppress = suppress
        self.raises = raises
        self.calls = 0

    def __enter__(self):
        return self

    def __exit__(self, typ, value, tb):
        self.calls += 1
        self.events.append((self.name, typ, value, tb is not None))
        if self.raises:
            raise CleanupError(self.name)
        return self.suppress


def context(events, *, twice=False, raise_before=False, raise_after=False):
    class Custom(click.Context):
        def close(self):
            events.append("before")
            if raise_before:
                raise CleanupError("before")
            super().close()
            if twice:
                self.with_resource(Resource("second", events))
                super().close()
            if raise_after:
                raise CleanupError("after")
            events.append("after")

    return Custom(click.Command("test"))


def assert_reusable(ctx):
    assert click.get_current_context(silent=True) is None
    assert ctx._depth == 0
    assert ctx._close_frame is None
    events = []
    resource = Resource("reused", events)
    with ctx:
        ctx.with_resource(resource)
    assert resource.calls == 1 and events[0][1:3] == (None, None)
    assert click.get_current_context(silent=True) is None


def override_and_suppression():
    for suppress in (False, True):
        events = []
        ctx = context(events)
        resource = Resource("resource", events, suppress=suppress)
        error = Boom("work")
        try:
            with ctx:
                ctx.with_resource(resource)
                raise error
        except Boom as observed:
            assert not suppress and observed is error
        else:
            assert suppress
        assert events[0] == "before" and events[2] == "after"
        assert events[1][:3] == ("resource", Boom, error)
        assert events[1][3] is True and resource.calls == 1
        assert_reusable(ctx)


def nested_suppression():
    for inner_suppresses, outer_suppresses in ((True, False), (False, True)):
        events = []
        ctx = context(events)
        inner = Resource("inner", events, suppress=inner_suppresses)
        outer = Resource("outer", events, suppress=outer_suppresses)
        with ctx:
            ctx.with_resource(outer)
            ctx.with_resource(inner)
            raise Boom("nested")
        assert [event if isinstance(event, str) else event[0] for event in events] == [
            "before", "inner", "outer", "after"
        ], events
        assert events[1][1] is Boom
        assert events[2][1] is (None if inner_suppresses else Boom)
        assert inner.calls == outer.calls == 1
        assert_reusable(ctx)


def replacement_exception():
    events = []
    ctx = context(events)
    resource = Resource("resource", events, raises=True)
    original = Boom("original")
    old_stack = ctx._exit_stack
    parent = click.Context(click.Command("parent"))
    with parent:
        try:
            with ctx:
                ctx.with_resource(resource)
                raise original
        except CleanupError as observed:
            assert observed.__context__ is original
            assert click.get_current_context() is parent
        else:
            raise AssertionError("cleanup exception was lost")
    assert events[0] == "before" and resource.calls == 1
    assert ctx._exit_stack is not old_stack
    assert_reusable(ctx)


def outer_suppresses_replacement():
    events = []
    ctx = context(events)
    outer = Resource("outer", events, suppress=True)
    inner = Resource("inner", events, raises=True)
    with ctx:
        ctx.with_resource(outer)
        ctx.with_resource(inner)
        raise Boom("original")
    assert [event if isinstance(event, str) else event[0] for event in events] == [
        "before", "inner", "outer", "after"
    ]
    assert events[2][1] is CleanupError
    assert outer.calls == inner.calls == 1
    assert_reusable(ctx)


def override_errors():
    for before in (True, False):
        events = []
        ctx = context(events, raise_before=before, raise_after=not before)
        resource = Resource("resource", events)
        try:
            with ctx:
                ctx.with_resource(resource)
        except CleanupError as observed:
            assert str(observed) == ("before" if before else "after")
        else:
            raise AssertionError("override exception was lost")
        assert resource.calls == (0 if before else 1)
        assert click.get_current_context(silent=True) is None
        assert ctx._close_frame is None and ctx._depth == 0


def double_close():
    events = []
    ctx = context(events, twice=True)
    resource = Resource("resource", events, suppress=True)
    with ctx:
        ctx.with_resource(resource)
        raise Boom("suppressed")
    assert resource.calls == 1
    assert [event if isinstance(event, str) else event[0] for event in events] == [
        "before", "resource", "second", "after"
    ]
    assert events[2][1:3] == (None, None)
    assert_reusable(ctx)


def explicit_close_and_nested_depth():
    events = []
    ctx = context(events)
    resource = Resource("manual", events)
    try:
        raise Boom("manual")
    except Boom:
        with ctx:
            ctx.with_resource(resource)
            assert ctx.close() is None
            assert click.get_current_context() is ctx
    assert resource.calls == 1 and events[1][1:3] == (None, None)
    nested = Resource("nested", events)
    with ctx:
        with ctx.scope(cleanup=False):
            ctx.with_resource(nested)
        assert nested.calls == 0
    assert nested.calls == 1
    assert_reusable(ctx)


for check in (
    override_and_suppression,
    nested_suppression,
    replacement_exception,
    outer_suppresses_replacement,
    override_errors,
    double_close,
    explicit_close_and_nested_depth,
):
    check()
    print(f"{check.__name__}: PASS")
