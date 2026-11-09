// SPDX-License-Identifier: Apache-2.0
// Round trip through the generated C# bindings of the "strings" case.
//
// tests/test_csharp_roundtrip.py builds this file together with the generated project and runs
// it with TXT_LIBRARY pointing at the built library.

using System.Text;
using Txt;
using Person = Txt.Text.Person;
using T = Txt.Text.Functions;
using V = Txt.Views.Functions;

static void Check(bool condition, string what)
{
    if (!condition)
    {
        throw new Exception("check failed: " + what);
    }
}

static TException Expect<TException>(Action action, string? message = null) where TException : Exception
{
    try
    {
        action();
    }
    catch (TException error)
    {
        if (message is not null && !error.Message.Contains(message))
        {
            throw new Exception($"'{error.Message}' lacks '{message}'");
        }
        return error;
    }
    throw new Exception($"no {typeof(TException).Name}");
}

// -- Text goes in as UTF-8 and comes back as a string -------------------------------------------

Check(T.Echo("héllo") == "héllo", "accents");
Check(T.Echo("日本語") == "日本語", "CJK");
Check(T.Echo("😀 outside the BMP") == "😀 outside the BMP", "surrogate pairs");
Check(T.Echo("") == "", "empty");
Check(T.Echo("a\0b") == "a\0b", "a std::string may hold a NUL");
Check(T.CountString("a\0b") == 3, "std::string has its size");
Check(T.CountCstr("abc") == 1003, "const char*");
string big = new string('x', 1 << 20) + "é";
Check(T.Echo(big) == big, "a big text");
Check(T.Concat("a\0", "\0b") == "a\0\0b", "concat");

// Text that is not valid is refused, instead of being replaced.
Expect<ArgumentNullException>(() => T.Echo(null!), "s");
Expect<ArgumentNullException>(() => T.CountCstr(null!), "s");
Expect<ArgumentException>(() => T.Echo("\ud800"), "not valid Unicode");
Expect<ArgumentException>(() => T.Echo("a\udc00b"), "not valid Unicode");
// A NUL would end a const char* for C++: it is refused.
Expect<ArgumentException>(() => T.CountCstr("a\0b"), "NUL");

Check(T.Length("abc", 7) == 3007, "a parameter named like the size of another");
Check(T.Total("a", "bc", "def") == 123, "total");
Check(T.Total(from_: "a", x_: "bc", arg3: "def") == 123, "named arguments");
Check(T.Alias("x") == "x!", "alias of std::string");
Check(T.CAlias("y") == "y", "alias of const char*");

// -- Results ---------------------------------------------------------------------------------

Check(T.MaybeNull(true) is null, "a NULL const char* is null");
Check(T.MaybeNull(false) == "not null", "a const char*");
Check(T.FirstChar(new string('x', 100)) == new string('x', 100), "first_char");
// A std::string result that is not UTF-8 is an error, not a replacement.
Expect<DecoderFallbackException>(() => T.Binary());
var error = Expect<BridgefexException>(() => T.Fail("boom é"), "boom é");
Check(error.Status == BridgefexException.Exception, "status");
Check(V.FirstWord("hello world") == "hello", "string_view in and out");
Check(V.SizeOf("abc") == 3, "string_view size");
Check(V.Empty() == "", "empty string_view");
Check(V.Literal() == "a literal", "literal");

// -- A class with text in its constructors and methods ----------------------------------------

using (var person = new Person("Ada", 36))
{
    Check(person.Name() == "Ada" && person.Age() == 36, "constructor");
    person.SetName("Grace");
    Check(person.NameRef() == "Grace" && person.CName() == "Grace", "set_name");
    Check(person.Greet("Bob", "Hi") == "Hi Bob, I am Grace", "greet");
    person.SetName("raw\0nul");
    Check(person.Name() == "raw\0nul", "a std::string keeps the NUL");
    Check(person.CName() == "raw", "a const char* stops at the NUL");
    Expect<ArgumentException>(() => person.Greet("Bob", "a\0b"), "NUL");
}
using (var eve = new Person("Eve"))
{
    Check(eve.Age() == 0, "the other constructor");
}
Check(Person.Species() == "human", "static method");

// Many threads: each one has its own texts.
var failures = new System.Collections.Concurrent.ConcurrentBag<string>();
var threads = new List<Thread>();
for (int number = 0; number < 8; number++)
{
    int mine = number;
    var thread = new Thread(() =>
    {
        for (int turn = 0; turn < 500; turn++)
        {
            string text = $"thread {mine} turn {turn} é😀";
            if (T.Echo(text) != text)
            {
                failures.Add(text);
            }
            try
            {
                T.Fail(text);
                failures.Add("no error");
            }
            catch (BridgefexException failure)
            {
                if (failure.Message != text)
                {
                    failures.Add(failure.Message);
                }
            }
        }
    });
    threads.Add(thread);
    thread.Start();
}
foreach (var thread in threads)
{
    thread.Join();
}
Check(failures.IsEmpty, "threads: " + string.Join("; ", failures.Take(3)));

Console.WriteLine("strings: C# round trip OK");
