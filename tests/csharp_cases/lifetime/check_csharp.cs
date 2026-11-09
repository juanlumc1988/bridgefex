// SPDX-License-Identifier: Apache-2.0
// The lifetime of the objects of the C# bindings under threads and under the garbage collector.
// Each section is a failure that an adversarial review found (a use after free of the C++
// object, a leak, or an object that did not look closed once it was).
//
// tests/test_csharp_roundtrip.py builds this file together with the generated project and runs
// it with LIFE_LIBRARY pointing at the built library.

using System.Runtime.CompilerServices;
using Life;
using Res = Life.Life.Res;
using Holder = Life.Life.Holder;
using F = Life.Life.Functions;

static void Check(bool condition, string what)
{
    if (!condition)
    {
        throw new Exception("check failed: " + what);
    }
}

static T Expect<T>(Action action, string? message = null) where T : Exception
{
    try
    {
        action();
    }
    catch (T error)
    {
        if (message is not null && !error.Message.Contains(message))
        {
            throw new Exception($"'{error.Message}' lacks '{message}'");
        }
        return error;
    }
    throw new Exception($"no {typeof(T).Name}");
}

static void Collect()
{
    GC.Collect();
    GC.WaitForPendingFinalizers();
    GC.Collect();
    GC.WaitForPendingFinalizers();
}

// An object is in a call of another thread for a while.
static Thread InCall(Res res, int milliseconds, out int[] seen)
{
    var result = new int[] { -1 };
    seen = result;
    var thread = new Thread(() => result[0] = res.SlowId(milliseconds));
    thread.Start();
    Thread.Sleep(100);  // it is inside the C++ method now
    return thread;
}

// 1. An object that another thread is using is not given to C++ (which would destroy it under that call).
{
    var res = new Res(5);
    var thread = InCall(res, 400, out var seen);
    Expect<InvalidOperationException>(() => F.Consume(res), "another call uses it");
    Check(!res.IsClosed, "the object is still open after the refusal");
    thread.Join();
    Check(seen[0] == 5, "the call that was running finished");
    Check(F.Consume(res) == 5, "and now it can be given");
}

// 1b. The same when the object the other thread uses is the owner of a borrowed one.
{
    var holder = new Holder();
    var inner = holder.Inner()!;
    var thread = new Thread(() => inner.SlowId(400));
    thread.Start();
    Thread.Sleep(100);
    holder.Dispose();
    Check(holder.IsClosed && inner.IsClosed, "closed, although a call is running");
    Expect<ObjectDisposedException>(() => inner.Id(), "closed");
    thread.Join();  // the holder is destroyed when that call ends, not under it
    Check(Holder.Live() == 0, "destroyed after the call");
}

// 5. A closed object looks closed, also while a call of another thread is using it.
{
    var res = new Res(6);
    var thread = InCall(res, 300, out var seen);
    res.Dispose();
    Check(res.IsClosed, "IsClosed after Dispose");
    Expect<ObjectDisposedException>(() => res.Id(), "closed");
    Expect<ObjectDisposedException>(() => res.Set(1), "closed");
    Expect<ObjectDisposedException>(() => F.Consume(res), "closed");
    thread.Join();
    Check(seen[0] == 6, "the running call saw its object");
    Check(Res.Live() == 0, "destroyed when the call ended");
}

// 2. An object that the C++ object of another one points to is not given either.
{
    var holder = new Holder();
    var res = new Res(7);
    holder.Set(res);
    Expect<InvalidOperationException>(() => F.Consume(res), "points to it");
    Check(holder.Peek() == 7 && !res.IsClosed, "the pointer is still good");
    holder.Dispose();
    Check(F.Consume(res) == 7, "given once nothing points to it");
}

// 3. A call that stores a pointer and then throws has kept it.
[MethodImpl(MethodImplOptions.NoInlining)]
static void StoreAndFail(Holder holder, int id)
{
    Expect<BridgefexException>(() => holder.SetThenThrow(new Res(id)), "stored, then failed");
}

{
    var holder = new Holder();
    StoreAndFail(holder, 8);
    Collect();
    Check(holder.Peek() == 8 && Res.Live() == 2, "what the failed call stored is alive (and the holder's own Res)");
    holder.Dispose();
    Collect();
    Check(Res.Live() == 0 && Holder.BadDestructions() == 0, "and is released with the holder");
}

// 4. An object that points to a part of itself, or two that point to each other, are destroyed when they are closed.
{
    var holder = new Holder();
    holder.Set(holder.Inner()!);
    holder.Dispose();
    Collect();
    Check(Holder.Live() == 0 && Res.Live() == 0, "a self-reference is destroyed");

    var first = new Holder();
    var second = new Holder();
    first.Set(second.Inner()!);
    second.Set(first.Inner()!);
    first.Dispose();
    second.Dispose();
    Collect();
    Check(Holder.Live() == 0 && Res.Live() == 0, "two that point to each other are destroyed when closed");
}

// The collector destroys what points before what is pointed to, whatever order it finds them in.
[MethodImpl(MethodImplOptions.NoInlining)]
static void ForgetPairs(int count)
{
    for (int i = 0; i < count; i++)
    {
        var holder = new Holder();
        holder.Set(new Res(i));
        var borrowed = new Holder();
        borrowed.Set(holder.Inner()!);
    }
}

{
    int badBefore = Holder.BadDestructions();  // two holders that point to each other cannot both be first
    ForgetPairs(300);
    Collect();
    Check(Holder.Live() == 0 && Res.Live() == 0, "everything was collected");
    Check(Holder.BadDestructions() == badBefore, "no destructor found what it points to destroyed");
}

// Closing an object that is pointed to destroys it all the same: that is up to the user.
{
    var holder = new Holder();
    var res = new Res(9);
    holder.Set(res);
    res.Dispose();
    Check(res.IsClosed, "closed although it is pointed to");
    Expect<ObjectDisposedException>(() => res.Id(), "closed");
    holder.Set(holder.Inner()!);  // do not leave the holder pointing to it
    holder.Dispose();
}

// Many threads giving, using and closing the same object: it is destroyed exactly once, and no
// call runs on a destroyed object.
for (int round = 0; round < 200; round++)
{
    var res = new Res(round);
    var barrier = new Barrier(3);
    int given = 0;
    int refused = 0;
    var threads = new List<Thread>
    {
        new(() => { barrier.SignalAndWait(); try { F.Consume(res); Interlocked.Increment(ref given); } catch (Exception e) when (e is InvalidOperationException or ObjectDisposedException) { Interlocked.Increment(ref refused); } }),
        new(() => { barrier.SignalAndWait(); try { res.Id(); } catch (ObjectDisposedException) { } catch (InvalidOperationException) { } }),
        new(() => { barrier.SignalAndWait(); res.Dispose(); }),
    };
    foreach (var thread in threads)
    {
        thread.Start();
    }
    foreach (var thread in threads)
    {
        thread.Join();
    }
    Check(given + refused == 1, $"one attempt to give, round {round}");
}
Check(Res.Live() == 0, "every object of the race was destroyed once");

Console.WriteLine("lifetime: C# round trip OK");
