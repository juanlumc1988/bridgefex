// SPDX-License-Identifier: Apache-2.0
// Plays the conformance scenarios (see tests/conformance/scenarios.py) against the C# bindings.
//
//   Runner <data.json>
//
// data.json has the scenarios and, for every function, class, constructor and method, the name
// of the C function it wraps (the bindings tell it with [BridgefexFunction]): the runner finds
// what to call by that name, and uses nothing but the generated code, as a user would.
using System.Numerics;
using System.Reflection;
using System.Text.Json;
using BridgefexRunner;

if (args.Length != 1)
{
    Console.Error.WriteLine("usage: Runner <data.json>");
    return 2;
}
using JsonDocument document = JsonDocument.Parse(File.ReadAllText(args[0]));
var player = new Player(document.RootElement.GetProperty("map"));
int failures = 0, steps = 0, count = 0;
foreach (JsonElement scenario in document.RootElement.GetProperty("scenarios").EnumerateArray())
{
    count++;
    string name = scenario.GetProperty("name").GetString()!;
    int number = 0;
    foreach (JsonElement step in scenario.GetProperty("steps").EnumerateArray())
    {
        number++;
        steps++;
        try
        {
            player.Step(step);
        }
        catch (Exception error)
        {
            failures++;
            string detail = error is Failure ? error.Message : error.ToString();
            Console.Error.WriteLine($"FAILED {name}, step {number} {step}: {detail}");
            break;
        }
    }
    player.Finish();
}
if (failures != 0)
{
    return 1;
}
Console.WriteLine($"conformance OK: {count} scenarios, {steps} steps (csharp)");
return 0;

namespace BridgefexRunner
{
    internal sealed class Failure : Exception
    {
        public Failure(string message) : base(message) { }
    }

    internal sealed class Player
    {
        private readonly Dictionary<string, MethodBase> _functions = new();
        private readonly JsonElement _map;
        private readonly Dictionary<string, object?> _variables = new();
        private readonly Dictionary<string, Type> _types = new();

        public Player(JsonElement map)
        {
            _map = map;
            foreach (Assembly assembly in AppDomain.CurrentDomain.GetAssemblies())
            {
                if (assembly.IsDynamic)
                {
                    continue;
                }
                Type[] types;
                try
                {
                    types = assembly.GetTypes();
                }
                catch (ReflectionTypeLoadException error)
                {
                    types = error.Types.Where(type => type is not null).ToArray()!;
                }
                foreach (Type type in types)
                {
                    if (type.FullName is { } full)
                    {
                        _types[full] = type;
                    }
                    foreach (MethodBase member in type.GetMembers(BindingFlags.Public | BindingFlags.Instance | BindingFlags.Static | BindingFlags.DeclaredOnly).OfType<MethodBase>())
                    {
                        foreach (CustomAttributeData data in member.CustomAttributes)
                        {
                            if (data.AttributeType.Name == "BridgefexFunctionAttribute")
                            {
                                _functions[(string)data.ConstructorArguments[0].Value!] = member;
                            }
                        }
                    }
                }
            }
        }

        // -- the map ---------------------------------------------------------------

        private JsonElement Section(string name) => _map.GetProperty(name);

        /// <summary>The member that wraps <paramref name="cName"/>, in the class of the receiver
        /// or the nearest one that has it (a class repeats the methods of the bases it does not
        /// derive from).</summary>
        private MethodBase Function(string cName, Type? receiver)
        {
            for (Type? type = receiver; type is not null; type = type.BaseType)
            {
                foreach (MethodBase member in type.GetMembers(BindingFlags.Public | BindingFlags.Instance | BindingFlags.Static | BindingFlags.DeclaredOnly).OfType<MethodBase>())
                {
                    foreach (CustomAttributeData data in member.CustomAttributes)
                    {
                        if (data.AttributeType.Name == "BridgefexFunctionAttribute" && (string)data.ConstructorArguments[0].Value! == cName)
                        {
                            return member;
                        }
                    }
                }
            }
            return Function(cName);
        }

        private MethodBase Function(string cName)
        {
            if (!_functions.TryGetValue(cName, out MethodBase? found))
            {
                throw new Failure($"the bindings have no function for {cName}");
            }
            return found;
        }

        private Type TypeNamed(string name)
        {
            if (!_types.TryGetValue(name, out Type? found))
            {
                throw new Failure($"the bindings have no type {name}");
            }
            return found;
        }

        // -- values ----------------------------------------------------------------

        private object? Decode(JsonElement value, Type target)
        {
            Type type = Nullable.GetUnderlyingType(target) ?? target;
            switch (value.ValueKind)
            {
                case JsonValueKind.True:
                case JsonValueKind.False:
                    if (type != typeof(bool))
                    {
                        throw new Failure($"a boolean for a {type.Name}");
                    }
                    return value.GetBoolean();
                case JsonValueKind.String:
                    if (type != typeof(string))
                    {
                        throw new Failure($"a string for a {type.Name}");
                    }
                    return value.GetString();
                case JsonValueKind.Number:
                    return Number(value, type);
                case JsonValueKind.Object:
                    return Conversion(Table(value, type), type);
                case JsonValueKind.Array:
                    if (!type.IsArray)
                    {
                        throw new Failure($"a list for a {type.Name}");
                    }
                    Type element = type.GetElementType()!;
                    Array items = Array.CreateInstance(element, value.GetArrayLength());
                    int position = 0;
                    foreach (JsonElement item in value.EnumerateArray())
                    {
                        items.SetValue(Decode(item, element), position++);
                    }
                    return items;
                default:
                    throw new Failure($"a value of kind {value.ValueKind} for a {type.Name}");
            }
        }

        /// <summary>An object where another class is expected: the implicit conversion that the
        /// class defines (a class converts to the bases it does not derive from).</summary>
        private static object? Conversion(object? value, Type target)
        {
            if (value is null || target.IsInstanceOfType(value) || value.GetType().IsValueType)
            {
                return value;
            }
            foreach (Type type in new[] { value.GetType(), target })
            {
                foreach (MethodInfo method in type.GetMethods(BindingFlags.Public | BindingFlags.Static))
                {
                    if (method.Name == "op_Implicit" && method.ReturnType == target && method.GetParameters()[0].ParameterType.IsInstanceOfType(value))
                    {
                        return method.Invoke(null, new[] { value });
                    }
                }
            }
            return value;
        }

        private static object Number(JsonElement value, Type type)
        {
            if (type == typeof(double) || type == typeof(float))
            {
                double number = value.GetDouble();
                if (type == typeof(float))
                {
                    if (double.IsFinite(number) && Math.Abs(number) > float.MaxValue)
                    {
                        throw new OverflowException("does not fit a float");
                    }
                    return (float)number;
                }
                return number;
            }
            BigInteger integer;
            if (value.TryGetInt64(out long small))
            {
                integer = small;
            }
            else if (value.TryGetUInt64(out ulong big))
            {
                integer = big;
            }
            else
            {
                throw new Failure($"{value} is not an integer for a {type.Name}");
            }
            return FromBig(integer, type);
        }

        private static object FromBig(BigInteger integer, Type type)
        {
            if (type.IsEnum)
            {
                return Enum.ToObject(type, FromBig(integer, Enum.GetUnderlyingType(type)));
            }
            if (type == typeof(sbyte)) return checked((sbyte)integer);
            if (type == typeof(byte)) return checked((byte)integer);
            if (type == typeof(short)) return checked((short)integer);
            if (type == typeof(ushort)) return checked((ushort)integer);
            if (type == typeof(int)) return checked((int)integer);
            if (type == typeof(uint)) return checked((uint)integer);
            if (type == typeof(long)) return checked((long)integer);
            if (type == typeof(ulong)) return checked((ulong)integer);
            if (type == typeof(nuint)) return checked((nuint)(ulong)integer);
            if (type == typeof(nint)) return checked((nint)(long)integer);
            throw new Failure($"an integer for a {type.Name}");
        }

        private object? Table(JsonElement value, Type type)
        {
            if (value.TryGetProperty("int", out JsonElement text))
            {
                return FromBig(BigInteger.Parse(text.GetString()!), type);
            }
            if (value.TryGetProperty("null", out _))
            {
                return null;
            }
            if (value.TryGetProperty("float", out JsonElement special))
            {
                double number = Special(special.GetString()!);
                if (type == typeof(float))
                {
                    return (float)number;
                }
                return number;
            }
            if (value.TryGetProperty("ref", out JsonElement name))
            {
                return _variables[name.GetString()!];
            }
            if (value.TryGetProperty("enum", out JsonElement enumName))
            {
                Type enumType = TypeNamed(Section("enums").GetProperty(enumName.GetString()!).GetString()!);
                return Enum.Parse(enumType, Member(value.GetProperty("member").GetString()!));
            }
            if (value.TryGetProperty("struct", out JsonElement structName))
            {
                Type structType = TypeNamed(Section("structs").GetProperty(structName.GetString()!).GetString()!);
                object boxed = Activator.CreateInstance(structType)!;
                FieldInfo[] fields = structType.GetFields(BindingFlags.Public | BindingFlags.Instance);
                JsonProperty[] given = value.EnumerateObject().Where(property => property.Name != "struct").ToArray();
                if (given.Length != fields.Length)
                {
                    throw new Failure($"{given.Length} members for the {fields.Length} of {structType.Name}");
                }
                for (int position = 0; position < fields.Length; position++)
                {
                    fields[position].SetValue(boxed, Decode(given[position].Value, fields[position].FieldType));
                }
                return boxed;
            }
            throw new Failure($"a table value for a {type.Name}: {value}");
        }

        private static double Special(string name) => name switch
        {
            "nan" => double.NaN,
            "inf" => double.PositiveInfinity,
            _ => double.NegativeInfinity,
        };

        private static string Member(string name) => Names.Pascal(name);

        private static bool IsIntegral(object? value) =>
            value is sbyte or byte or short or ushort or int or uint or long or ulong or nint or nuint;

        private static BigInteger Big(object value) => value switch
        {
            sbyte v => v,
            byte v => v,
            short v => v,
            ushort v => v,
            int v => v,
            uint v => v,
            long v => v,
            ulong v => v,
            nint v => v,
            nuint v => v,
            _ => throw new Failure($"{value} is not an integer"),
        };

        private string? Differences(object? actual, JsonElement expected, double approx, string where)
        {
            switch (expected.ValueKind)
            {
                case JsonValueKind.True:
                case JsonValueKind.False:
                    return actual is bool b && b == expected.GetBoolean() ? null : $"{where}: {Show(actual)}, not {expected}";
                case JsonValueKind.String:
                    return actual is string s && s == expected.GetString() ? null : $"{where}: {Show(actual)}, not {expected}";
                case JsonValueKind.Number:
                    if (expected.TryGetInt64(out long small))
                    {
                        return IsIntegral(actual) && Big(actual!) == small ? null : $"{where}: {Show(actual)}, not {expected}";
                    }
                    if (expected.TryGetUInt64(out ulong big))
                    {
                        return IsIntegral(actual) && Big(actual!) == big ? null : $"{where}: {Show(actual)}, not {expected}";
                    }
                    return Floating(actual, expected.GetDouble(), approx, where);
                case JsonValueKind.Object:
                    if (expected.TryGetProperty("int", out JsonElement text))
                    {
                        return IsIntegral(actual) && Big(actual!) == BigInteger.Parse(text.GetString()!) ? null : $"{where}: {Show(actual)}, not {text}";
                    }
                    if (expected.TryGetProperty("null", out _))
                    {
                        return actual is null ? null : $"{where}: {Show(actual)}, not null";
                    }
                    if (expected.TryGetProperty("float", out JsonElement special))
                    {
                        return Floating(actual, Special(special.GetString()!), approx, where);
                    }
                    if (expected.TryGetProperty("struct", out JsonElement structName))
                    {
                        Type structType = TypeNamed(Section("structs").GetProperty(structName.GetString()!).GetString()!);
                        if (actual is null || actual.GetType() != structType)
                        {
                            return $"{where}: {Show(actual)}, not {structName}";
                        }
                        FieldInfo[] fields = structType.GetFields(BindingFlags.Public | BindingFlags.Instance);
                        JsonProperty[] members = expected.EnumerateObject().Where(property => property.Name != "struct").ToArray();
                        for (int position = 0; position < fields.Length; position++)
                        {
                            string? problem = Differences(fields[position].GetValue(actual), members[position].Value, approx, $"{where}.{members[position].Name}");
                            if (problem is not null)
                            {
                                return problem;
                            }
                        }
                        return null;
                    }
                    if (expected.TryGetProperty("enum", out JsonElement enumName))
                    {
                        Type enumType = TypeNamed(Section("enums").GetProperty(enumName.GetString()!).GetString()!);
                        string member = Member(expected.GetProperty("member").GetString()!);
                        return actual is not null && actual.GetType() == enumType && actual.ToString() == member ? null : $"{where}: {Show(actual)}, not {enumName}.{member}";
                    }
                    break;
            }
            if (expected.ValueKind == JsonValueKind.Array)
            {
                if (actual is not Array list || list.Length != expected.GetArrayLength())
                {
                    return $"{where}: {Show(actual)}, not {expected}";
                }
                int index = 0;
                foreach (JsonElement item in expected.EnumerateArray())
                {
                    string? problem = Differences(list.GetValue(index), item, approx, $"{where}[{index}]");
                    if (problem is not null)
                    {
                        return problem;
                    }
                    index++;
                }
                return null;
            }
            throw new Failure($"cannot compare with {expected}");
        }

        private static string? Floating(object? actual, double wanted, double approx, string where)
        {
            double value;
            if (actual is double d)
            {
                value = d;
            }
            else if (actual is float f)
            {
                value = f;
            }
            else
            {
                return $"{where}: {Show(actual)}, not {wanted}";
            }
            bool same = value == wanted || (double.IsNaN(value) && double.IsNaN(wanted)) || (approx > 0 && Math.Abs(value - wanted) <= approx);
            return same ? null : $"{where}: {Show(actual)}, not {wanted}";
        }

        private static string Show(object? value) => value is null ? "null" : $"{value} ({value.GetType().Name})";

        // -- steps -----------------------------------------------------------------

        public void Step(JsonElement step)
        {
            if (step.TryGetProperty("close", out JsonElement closing))
            {
                ((IDisposable)_variables[closing.GetString()!]!).Dispose();
                return;
            }
            if (step.TryGetProperty("derive", out _))
            {
                throw new Failure("overriding virtual methods is not supported in C# yet");
            }
            JsonElement arguments = step.TryGetProperty("args", out JsonElement given) ? given : default;
            Func<object?> call;
            if (step.TryGetProperty("new", out JsonElement created))
            {
                var constructor = (ConstructorInfo)Function(step.GetProperty("c").GetString()!);
                call = () => constructor.Invoke(Convert(arguments, constructor.GetParameters()));
            }
            else
            {
                string target = step.GetProperty("call").GetString()!;
                string[] parts = target.Split('.');
                object? instance = parts.Length == 2 && _variables.TryGetValue(parts[0], out object? found) ? found : null;
                MethodBase method = Function(step.GetProperty("c").GetString()!, instance?.GetType());
                call = () => method.Invoke(instance, Convert(arguments, method.GetParameters()));
            }
            object? result = Attempt(call, step);
            if (step.TryGetProperty("as", out JsonElement name))
            {
                _variables[name.GetString()!] = result;
            }
            if (step.TryGetProperty("expect", out JsonElement expected))
            {
                double approx = step.TryGetProperty("approx", out JsonElement tolerance) ? tolerance.GetDouble() : 0;
                string? problem = Differences(result, expected, approx, "result");
                if (problem is not null)
                {
                    throw new Failure(problem);
                }
            }
        }

        private object?[] Convert(JsonElement arguments, ParameterInfo[] parameters)
        {
            int count = arguments.ValueKind == JsonValueKind.Array ? arguments.GetArrayLength() : 0;
            if (count != parameters.Length)
            {
                throw new Failure($"{count} arguments for {parameters.Length} parameters");
            }
            var result = new object?[count];
            for (int position = 0; position < count; position++)
            {
                result[position] = Decode(arguments[position], parameters[position].ParameterType);
            }
            return result;
        }

        private static readonly object Nothing = new();

        private object? Attempt(Func<object?> call, JsonElement step)
        {
            string? kind = step.TryGetProperty("error", out JsonElement error) ? error.GetString() : null;
            object? result;
            try
            {
                result = call();
            }
            catch (Exception caught)
            {
                Exception inner = caught is TargetInvocationException { InnerException: { } deeper } ? deeper : caught;
                if (kind is null)
                {
                    throw inner;
                }
                if (!Matches(inner, kind))
                {
                    throw new Failure($"{inner.GetType().Name} ({inner.Message}), not a {kind} error");
                }
                if (step.TryGetProperty("message", out JsonElement message) && !inner.Message.Contains(message.GetString()!))
                {
                    throw new Failure($"the message '{inner.Message}' lacks '{message.GetString()}'");
                }
                return Nothing;
            }
            if (kind is not null)
            {
                throw new Failure($"no {kind} error; the result is {Show(result)}");
            }
            return result;
        }

        private static bool Matches(Exception error, string kind)
        {
            Type bridgefex = error.GetType();
            bool IsStatus(int status) => bridgefex.Name == "BridgefexException" && (int)bridgefex.GetProperty("Status")!.GetValue(error)! == status;
            return kind switch
            {
                "exception" => IsStatus(1),
                "unknown_exception" => IsStatus(2),
                "out_of_memory" => IsStatus(4),
                "range" => error is OverflowException or ArgumentOutOfRangeException,
                "length" => error is ArgumentException && error.Message.Contains("elements"),
                "closed" => error is ObjectDisposedException,
                _ => false,
            };
        }

        public void Finish()
        {
            foreach (object? item in _variables.Values.Reverse())
            {
                (item as IDisposable)?.Dispose();
            }
            _variables.Clear();
        }
    }

    internal static class Names
    {
        public static string Pascal(string name)
        {
            var result = new System.Text.StringBuilder();
            foreach (string part in System.Text.RegularExpressions.Regex.Matches(name, "[A-Za-z0-9]+").Select(match => match.Value))
            {
                result.Append(char.ToUpperInvariant(part[0])).Append(part, 1, part.Length - 1);
            }
            string text = result.ToString();
            return text.Length == 0 ? "_" : char.IsDigit(text[0]) ? "_" + text : text;
        }
    }
}
