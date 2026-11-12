// SPDX-License-Identifier: Apache-2.0
// Plays the conformance scenarios (see tests/conformance/scenarios.py) against the Java bindings.
//
//   java Runner.java <data.json>
//
// data.json has the scenarios and, for every function, class, constructor and method, the name
// of the C function it wraps (the bindings tell it with @BridgefexFunction): the runner finds
// what to call by that name, and uses nothing but the generated code, as a user would.

import java.io.IOException;
import java.lang.annotation.Annotation;
import java.lang.reflect.Array;
import java.lang.reflect.Constructor;
import java.lang.reflect.Executable;
import java.lang.reflect.Field;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.lang.reflect.Parameter;
import java.math.BigInteger;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

public final class Runner {
    private Runner() {
    }

    public static void main(String[] args) throws IOException {
        if (args.length != 1) {
            System.err.println("usage: Runner <data.json>");
            System.exit(2);
        }
        Object document = new Json(Files.readString(Path.of(args[0]))).parse();
        Map<String, Object> root = Json.object(document);
        Player player = new Player(Json.object(root.get("map")));
        int failures = 0;
        int steps = 0;
        int count = 0;
        for (Object item : Json.list(root.get("scenarios"))) {
            Map<String, Object> scenario = Json.object(item);
            count++;
            String name = (String) scenario.get("name");
            int number = 0;
            for (Object stepItem : Json.list(scenario.get("steps"))) {
                number++;
                steps++;
                try {
                    player.step(Json.object(stepItem));
                } catch (Throwable error) {
                    failures++;
                    String detail = error instanceof Failure ? error.getMessage() : stack(error);
                    System.err.println("FAILED " + name + ", step " + number + " " + stepItem + ": " + detail);
                    break;
                }
            }
            player.finish();
        }
        if (failures != 0) {
            System.exit(1);
        }
        System.out.println("conformance OK: " + count + " scenarios, " + steps + " steps (jvm)");
    }

    private static String stack(Throwable error) {
        StringBuilder text = new StringBuilder(error.toString());
        for (StackTraceElement element : error.getStackTrace()) {
            text.append("\n    at ").append(element);
            if (text.length() > 1500) {
                break;
            }
        }
        if (error.getCause() != null) {
            text.append("\n  caused by ").append(stack(error.getCause()));
        }
        return text.toString();
    }

    /** What a scenario found wrong: the bindings did something else than it says. */
    static final class Failure extends RuntimeException {
        private static final long serialVersionUID = 1L;

        Failure(String message) {
            super(message);
        }
    }

    /** An argument that the type of the parameter cannot hold: the scenarios call it a range error. */
    static final class RangeFailure extends RuntimeException {
        private static final long serialVersionUID = 1L;

        RangeFailure(String message) {
            super(message);
        }
    }

    // -- JSON --------------------------------------------------------------------------------

    /** A JSON reader: objects are maps (in order), arrays lists, integers BigInteger, other numbers
     * Double. The JDK has none. */
    static final class Json {
        private final String text;
        private int at;

        Json(String text) {
            this.text = text;
        }

        @SuppressWarnings("unchecked")
        static Map<String, Object> object(Object value) {
            return (Map<String, Object>) value;
        }

        @SuppressWarnings("unchecked")
        static List<Object> list(Object value) {
            return (List<Object>) value;
        }

        Object parse() {
            Object value = value();
            skip();
            if (at != text.length()) {
                throw new IllegalArgumentException("text after the JSON value at " + at);
            }
            return value;
        }

        private void skip() {
            while (at < text.length() && Character.isWhitespace(text.charAt(at))) {
                at++;
            }
        }

        private Object value() {
            skip();
            char c = text.charAt(at);
            switch (c) {
                case '{':
                    return readObject();
                case '[':
                    return readArray();
                case '"':
                    return readString();
                case 't':
                    at += 4;
                    return Boolean.TRUE;
                case 'f':
                    at += 5;
                    return Boolean.FALSE;
                case 'n':
                    at += 4;
                    return null;
                default:
                    return readNumber();
            }
        }

        private Map<String, Object> readObject() {
            Map<String, Object> result = new LinkedHashMap<>();
            at++;
            skip();
            if (text.charAt(at) == '}') {
                at++;
                return result;
            }
            while (true) {
                skip();
                String key = readString();
                skip();
                at++; // :
                result.put(key, value());
                skip();
                char c = text.charAt(at++);
                if (c == '}') {
                    return result;
                }
            }
        }

        private List<Object> readArray() {
            List<Object> result = new ArrayList<>();
            at++;
            skip();
            if (text.charAt(at) == ']') {
                at++;
                return result;
            }
            while (true) {
                result.add(value());
                skip();
                char c = text.charAt(at++);
                if (c == ']') {
                    return result;
                }
            }
        }

        private String readString() {
            StringBuilder result = new StringBuilder();
            at++;
            while (true) {
                char c = text.charAt(at++);
                if (c == '"') {
                    return result.toString();
                }
                if (c != '\\') {
                    result.append(c);
                    continue;
                }
                char escaped = text.charAt(at++);
                switch (escaped) {
                    case 'n' -> result.append('\n');
                    case 't' -> result.append('\t');
                    case 'r' -> result.append('\r');
                    case 'b' -> result.append('\b');
                    case 'f' -> result.append('\f');
                    case 'u' -> {
                        result.append((char) Integer.parseInt(text.substring(at, at + 4), 16));
                        at += 4;
                    }
                    default -> result.append(escaped);
                }
            }
        }

        private Object readNumber() {
            int start = at;
            while (at < text.length() && "+-0123456789.eE".indexOf(text.charAt(at)) >= 0) {
                at++;
            }
            String number = text.substring(start, at);
            if (number.contains(".") || number.contains("e") || number.contains("E")) {
                return Double.valueOf(number);
            }
            return new BigInteger(number);
        }
    }

    // -- the player --------------------------------------------------------------------------

    static final class Player {
        private final Map<String, Object> map;
        private final Map<String, Executable> functions = new LinkedHashMap<>();
        private final Map<String, Class<?>> types = new LinkedHashMap<>();
        private final Map<String, Object> variables = new LinkedHashMap<>();

        Player(Map<String, Object> map) {
            this.map = map;
            for (Object name : Json.list(map.get("types"))) {
                Class<?> type;
                try {
                    type = Class.forName((String) name);
                } catch (ClassNotFoundException error) {
                    throw new Failure("the bindings have no type " + name);
                }
                types.put(type.getName(), type);
                for (Method method : type.getDeclaredMethods()) {
                    index(method);
                }
                for (Constructor<?> constructor : type.getDeclaredConstructors()) {
                    index(constructor);
                }
            }
        }

        private void index(Executable member) {
            if (!Modifier.isPublic(member.getModifiers())) {
                return;
            }
            for (Annotation annotation : member.getAnnotations()) {
                if (annotation.annotationType().getSimpleName().equals("BridgefexFunction")) {
                    try {
                        functions.put((String) annotation.annotationType().getMethod("value").invoke(annotation), member);
                    } catch (ReflectiveOperationException error) {
                        throw new Failure(error.toString());
                    }
                }
            }
        }

        private Executable function(String cName, Class<?> receiver) {
            for (Class<?> type = receiver; type != null; type = type.getSuperclass()) {
                for (Method method : type.getDeclaredMethods()) {
                    if (cName.equals(nameOf(method))) {
                        return method;
                    }
                }
            }
            return function(cName);
        }

        private static String nameOf(Executable member) {
            for (Annotation annotation : member.getAnnotations()) {
                if (annotation.annotationType().getSimpleName().equals("BridgefexFunction")) {
                    try {
                        return (String) annotation.annotationType().getMethod("value").invoke(annotation);
                    } catch (ReflectiveOperationException error) {
                        throw new Failure(error.toString());
                    }
                }
            }
            return null;
        }

        private Executable function(String cName) {
            Executable found = functions.get(cName);
            if (found == null) {
                throw new Failure("the bindings have no function for " + cName);
            }
            return found;
        }

        private Class<?> typeNamed(String name) {
            Class<?> found = types.get(name);
            if (found == null) {
                throw new Failure("the bindings have no type " + name);
            }
            return found;
        }

        private Map<String, Object> section(String name) {
            return Json.object(map.get(name));
        }

        private static boolean unsigned(Annotation[] annotations) {
            for (Annotation annotation : annotations) {
                if (annotation.annotationType().getSimpleName().equals("BridgefexUnsigned")) {
                    return true;
                }
            }
            return false;
        }

        // -- values ----------------------------------------------------------------------

        private Object decode(Object value, Class<?> type, boolean unsigned) {
            if (value instanceof Boolean flag) {
                if (type != boolean.class) {
                    throw new Failure("a boolean for a " + type.getSimpleName());
                }
                return flag;
            }
            if (value instanceof String text) {
                if (type != String.class) {
                    throw new Failure("a string for a " + type.getSimpleName());
                }
                return text;
            }
            if (value instanceof BigInteger || value instanceof Double) {
                return number(value, type, unsigned);
            }
            if (value instanceof Map<?, ?>) {
                return conversion(table(Json.object(value), type, unsigned), type);
            }
            if (value instanceof List<?> items) {
                if (!type.isArray()) {
                    throw new Failure("a list for a " + type.getSimpleName());
                }
                Class<?> element = type.getComponentType();
                Object result = Array.newInstance(element, items.size());
                int position = 0;
                for (Object item : items) {
                    Array.set(result, position++, decode(item, element, unsigned));
                }
                return result;
            }
            throw new Failure("a value " + value + " for a " + type.getSimpleName());
        }

        /** An object where another class is expected: the conversion that the class defines. */
        private static Object conversion(Object value, Class<?> target) {
            if (value == null || target.isInstance(value) || value.getClass().isPrimitive()) {
                return value;
            }
            for (Method method : value.getClass().getMethods()) {
                if (method.getParameterCount() == 0 && method.getReturnType() == target
                    && method.getName().startsWith("as")) {
                    try {
                        return method.invoke(value);
                    } catch (ReflectiveOperationException error) {
                        throw new Failure(error.toString());
                    }
                }
            }
            return value;
        }

        private Object number(Object value, Class<?> type, boolean unsigned) {
            if (type == double.class || type == float.class) {
                double number = value instanceof Double d ? d : ((BigInteger) value).doubleValue();
                if (type == float.class) {
                    if (Double.isFinite(number) && Math.abs(number) > Float.MAX_VALUE) {
                        throw new RangeFailure("does not fit a float");
                    }
                    return (float) number;
                }
                return number;
            }
            if (value instanceof Double) {
                throw new Failure(value + " is not an integer for a " + type.getSimpleName());
            }
            return fromBig((BigInteger) value, type, unsigned);
        }

        private Object fromBig(BigInteger integer, Class<?> type, boolean unsigned) {
            if (type.isEnum()) {
                throw new Failure("an integer for the enum " + type.getSimpleName());
            }
            BigInteger low;
            BigInteger high;
            if (type == byte.class) {
                low = BigInteger.valueOf(Byte.MIN_VALUE);
                high = BigInteger.valueOf(Byte.MAX_VALUE);
            } else if (type == short.class) {
                low = BigInteger.valueOf(Short.MIN_VALUE);
                high = BigInteger.valueOf(Short.MAX_VALUE);
            } else if (type == int.class) {
                low = BigInteger.valueOf(Integer.MIN_VALUE);
                high = BigInteger.valueOf(Integer.MAX_VALUE);
            } else if (type == long.class) {
                low = unsigned ? BigInteger.ZERO : BigInteger.valueOf(Long.MIN_VALUE);
                high = unsigned ? BigInteger.ONE.shiftLeft(64).subtract(BigInteger.ONE) : BigInteger.valueOf(Long.MAX_VALUE);
            } else {
                throw new Failure("an integer for a " + type.getSimpleName());
            }
            if (integer.compareTo(low) < 0 || integer.compareTo(high) > 0) {
                throw new RangeFailure(integer + " does not fit a " + type.getSimpleName() + (unsigned ? " (unsigned)" : ""));
            }
            if (type == byte.class) {
                return integer.byteValue();
            }
            if (type == short.class) {
                return integer.shortValue();
            }
            if (type == int.class) {
                return integer.intValue();
            }
            return integer.longValue();
        }

        private Object table(Map<String, Object> value, Class<?> type, boolean unsigned) {
            if (value.containsKey("int")) {
                return fromBig(new BigInteger((String) value.get("int")), type, unsigned);
            }
            if (value.containsKey("null")) {
                return null;
            }
            if (value.containsKey("float")) {
                double number = special((String) value.get("float"));
                return type == float.class ? (Object) (float) number : (Object) number;
            }
            if (value.containsKey("ref")) {
                return variables.get((String) value.get("ref"));
            }
            if (value.containsKey("enum")) {
                String enumName = (String) value.get("enum");
                Class<?> enumType = typeNamed((String) section("enums").get(enumName));
                String member = (String) Json.object(section("members").get(enumName)).get((String) value.get("member"));
                for (Object constant : enumType.getEnumConstants()) {
                    if (((Enum<?>) constant).name().equals(member)) {
                        return constant;
                    }
                }
                throw new Failure("the enum " + enumName + " has no member " + member);
            }
            if (value.containsKey("struct")) {
                Class<?> structType = typeNamed((String) section("structs").get((String) value.get("struct")));
                List<Field> fields = fieldsOf(structType);
                List<Object> given = new ArrayList<>();
                for (Map.Entry<String, Object> entry : value.entrySet()) {
                    if (!entry.getKey().equals("struct")) {
                        given.add(entry.getValue());
                    }
                }
                if (given.size() != fields.size()) {
                    throw new Failure(given.size() + " members for the " + fields.size() + " of " + structType.getSimpleName());
                }
                try {
                    Object result = structType.getConstructor().newInstance();
                    for (int position = 0; position < fields.size(); position++) {
                        Field field = fields.get(position);
                        field.set(result, decode(given.get(position), field.getType(), unsigned(field.getAnnotations())));
                    }
                    return result;
                } catch (ReflectiveOperationException error) {
                    throw new Failure(error.toString());
                }
            }
            throw new Failure("a table value for a " + type.getSimpleName() + ": " + value);
        }

        private static List<Field> fieldsOf(Class<?> type) {
            List<Field> fields = new ArrayList<>();
            for (Field field : type.getDeclaredFields()) {
                if (Modifier.isPublic(field.getModifiers()) && !Modifier.isStatic(field.getModifiers())) {
                    fields.add(field);
                }
            }
            return fields;
        }

        private static double special(String name) {
            return switch (name) {
                case "nan" -> Double.NaN;
                case "inf" -> Double.POSITIVE_INFINITY;
                default -> Double.NEGATIVE_INFINITY;
            };
        }

        private static boolean isIntegral(Object value) {
            return value instanceof Byte || value instanceof Short || value instanceof Integer || value instanceof Long;
        }

        /** The value of an integer result: a 64-bit unsigned one is read as such. */
        private static BigInteger big(Object value, boolean unsigned) {
            long bits = ((Number) value).longValue();
            if (unsigned && value instanceof Long && bits < 0) {
                return BigInteger.valueOf(bits).add(BigInteger.ONE.shiftLeft(64));
            }
            return BigInteger.valueOf(bits);
        }

        private String differences(Object actual, Object expected, double approx, String where, boolean unsigned) {
            if (expected instanceof Boolean flag) {
                return actual instanceof Boolean b && b.equals(flag) ? null : where + ": " + show(actual) + ", not " + expected;
            }
            if (expected instanceof String text) {
                return actual instanceof String s && s.equals(text) ? null : where + ": " + show(actual) + ", not " + expected;
            }
            if (expected instanceof BigInteger wanted) {
                return isIntegral(actual) && big(actual, unsigned).equals(wanted) ? null : where + ": " + show(actual) + ", not " + expected;
            }
            if (expected instanceof Double wanted) {
                return floating(actual, wanted, approx, where);
            }
            if (expected instanceof List<?> items) {
                if (actual == null || !actual.getClass().isArray() || Array.getLength(actual) != items.size()) {
                    return where + ": " + show(actual) + ", not " + expected;
                }
                for (int index = 0; index < items.size(); index++) {
                    String problem = differences(Array.get(actual, index), items.get(index), approx, where + "[" + index + "]", unsigned);
                    if (problem != null) {
                        return problem;
                    }
                }
                return null;
            }
            if (expected instanceof Map<?, ?> raw) {
                Map<String, Object> table = Json.object(raw);
                if (table.containsKey("int")) {
                    return isIntegral(actual) && big(actual, unsigned).equals(new BigInteger((String) table.get("int"))) ? null
                        : where + ": " + show(actual) + ", not " + table.get("int");
                }
                if (table.containsKey("null")) {
                    return actual == null ? null : where + ": " + show(actual) + ", not null";
                }
                if (table.containsKey("float")) {
                    return floating(actual, special((String) table.get("float")), approx, where);
                }
                if (table.containsKey("struct")) {
                    Class<?> structType = typeNamed((String) section("structs").get((String) table.get("struct")));
                    if (actual == null || actual.getClass() != structType) {
                        return where + ": " + show(actual) + ", not " + table.get("struct");
                    }
                    List<Field> fields = fieldsOf(structType);
                    List<Map.Entry<String, Object>> members = new ArrayList<>();
                    for (Map.Entry<String, Object> entry : table.entrySet()) {
                        if (!entry.getKey().equals("struct")) {
                            members.add(entry);
                        }
                    }
                    for (int position = 0; position < fields.size(); position++) {
                        try {
                            Field field = fields.get(position);
                            String problem = differences(field.get(actual), members.get(position).getValue(), approx,
                                where + "." + members.get(position).getKey(), unsigned(field.getAnnotations()));
                            if (problem != null) {
                                return problem;
                            }
                        } catch (IllegalAccessException error) {
                            throw new Failure(error.toString());
                        }
                    }
                    return null;
                }
                if (table.containsKey("enum")) {
                    String enumName = (String) table.get("enum");
                    Class<?> enumType = typeNamed((String) section("enums").get(enumName));
                    String member = (String) Json.object(section("members").get(enumName)).get((String) table.get("member"));
                    return actual != null && actual.getClass() == enumType && ((Enum<?>) actual).name().equals(member) ? null
                        : where + ": " + show(actual) + ", not " + enumName + "." + member;
                }
            }
            throw new Failure("cannot compare with " + expected);
        }

        private static String floating(Object actual, double wanted, double approx, String where) {
            double value;
            if (actual instanceof Double d) {
                value = d;
            } else if (actual instanceof Float f) {
                value = f;
            } else {
                return where + ": " + show(actual) + ", not " + wanted;
            }
            boolean same = value == wanted || (Double.isNaN(value) && Double.isNaN(wanted))
                || (approx > 0 && Math.abs(value - wanted) <= approx);
            return same ? null : where + ": " + show(actual) + ", not " + wanted;
        }

        private static String show(Object value) {
            return value == null ? "null" : value + " (" + value.getClass().getSimpleName() + ")";
        }

        // -- steps -----------------------------------------------------------------------

        void step(Map<String, Object> step) {
            if (step.containsKey("close")) {
                close(variables.get((String) step.get("close")));
                return;
            }
            if (step.containsKey("derive")) {
                throw new Failure("overriding virtual methods is not supported in Java yet");
            }
            List<Object> arguments = step.containsKey("args") ? Json.list(step.get("args")) : List.of();
            Executable member;
            Object instance = null;
            if (step.containsKey("new")) {
                member = function((String) step.get("c"));
            } else {
                String target = (String) step.get("call");
                String[] parts = target.split("\\.");
                if (parts.length == 2 && variables.containsKey(parts[0])) {
                    instance = variables.get(parts[0]);
                }
                member = function((String) step.get("c"), instance == null ? null : instance.getClass());
            }
            final Executable called = member;
            final Object receiver = instance;
            boolean unsignedResult = member instanceof Method method && unsigned(method.getAnnotations());
            Object result = attempt(() -> invoke(called, receiver, convert(arguments, called)), step);
            if (step.containsKey("as")) {
                variables.put((String) step.get("as"), result);
            }
            if (step.containsKey("expect")) {
                double approx = step.containsKey("approx") ? ((Number) step.get("approx")).doubleValue() : 0;
                String problem = differences(result, step.get("expect"), approx, "result", unsignedResult);
                if (problem != null) {
                    throw new Failure(problem);
                }
            }
        }

        private Object[] convert(List<Object> arguments, Executable member) {
            Parameter[] parameters = member.getParameters();
            if (arguments.size() != parameters.length) {
                throw new Failure(arguments.size() + " arguments for " + parameters.length + " parameters");
            }
            Object[] result = new Object[parameters.length];
            for (int position = 0; position < parameters.length; position++) {
                result[position] = decode(arguments.get(position), parameters[position].getType(),
                    unsigned(parameters[position].getAnnotations()));
            }
            return result;
        }

        private static Object invoke(Executable member, Object instance, Object[] arguments) throws Exception {
            if (member instanceof Constructor<?> constructor) {
                return constructor.newInstance(arguments);
            }
            Method method = (Method) member;
            return method.invoke(Modifier.isStatic(method.getModifiers()) ? null : instance, arguments);
        }

        private interface Call {
            Object run() throws Exception;
        }

        private static final Object NOTHING = new Object();

        private Object attempt(Call call, Map<String, Object> step) {
            String kind = step.containsKey("error") ? (String) step.get("error") : null;
            Object result;
            try {
                result = call.run();
            } catch (Throwable caught) {
                Throwable inner = caught instanceof InvocationTargetException wrapped && wrapped.getCause() != null
                    ? wrapped.getCause() : caught;
                if (kind == null) {
                    if (inner instanceof RuntimeException runtime) {
                        throw runtime;
                    }
                    if (inner instanceof Error fatal) {
                        throw fatal;
                    }
                    throw new Failure(inner.toString());
                }
                if (!matches(inner, kind)) {
                    throw new Failure(inner.getClass().getSimpleName() + " (" + inner.getMessage() + "), not a " + kind + " error");
                }
                if (step.containsKey("message") && (inner.getMessage() == null
                    || !inner.getMessage().contains((String) step.get("message")))) {
                    throw new Failure("the message '" + inner.getMessage() + "' lacks '" + step.get("message") + "'");
                }
                return NOTHING;
            }
            if (kind != null) {
                throw new Failure("no " + kind + " error; the result is " + show(result));
            }
            return result;
        }

        private static boolean matches(Throwable error, String kind) {
            Integer status = null;
            if (error.getClass().getSimpleName().equals("BridgefexException")) {
                try {
                    status = (Integer) error.getClass().getMethod("status").invoke(error);
                } catch (ReflectiveOperationException wrong) {
                    throw new Failure(wrong.toString());
                }
            }
            return switch (kind) {
                case "exception" -> status != null && status == 1;
                case "unknown_exception" -> status != null && status == 2;
                case "out_of_memory" -> status != null && status == 4;
                case "range" -> error instanceof RangeFailure
                    || error instanceof IllegalArgumentException && !String.valueOf(error.getMessage()).contains("elements");
                case "length" -> error instanceof IllegalArgumentException && String.valueOf(error.getMessage()).contains("elements");
                case "closed" -> error instanceof IllegalStateException && String.valueOf(error.getMessage()).contains("closed");
                default -> false;
            };
        }

        private static void close(Object item) {
            if (item instanceof AutoCloseable closeable) {
                try {
                    closeable.close();
                } catch (RuntimeException error) {
                    throw error;
                } catch (Exception error) {
                    throw new Failure(error.toString());
                }
            }
        }

        void finish() {
            List<Object> items = new ArrayList<>(variables.values());
            for (int index = items.size() - 1; index >= 0; index--) {
                close(items.get(index));
            }
            variables.clear();
        }
    }
}
