// SPDX-License-Identifier: Apache-2.0
// A redeploy of an application that has the JNI bindings: its class loader is dropped and another one
// loads the bindings again, in the same JVM. The first loader has to be collected (a thread or a
// thread local of the bindings must not keep it alive), and then the library, which only one class
// loader can have, is free for the second. The arguments are the name of the class of an object of
// C++ that the bindings wrap (with a constructor without arguments, and a method value() that
// returns 42) and the library. With the argument "callbacks" instead, the deployment is that of the
// "callbacks" case: it gives callbacks to a function, to an object that it forgets, and to C++, which
// keeps one in a global when the deployment is dropped (the library is unloaded with its class loader).

import java.lang.ref.WeakReference;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.net.URL;
import java.net.URLClassLoader;
import java.util.function.BiConsumer;
import java.util.function.Consumer;
import java.util.function.Function;

public class Redeploy {
    // One deployment: it uses the bindings from this thread (the one that goes on after it), makes an
    // object that it closes and another that it forgets, and drops everything but a weak reference.
    private static WeakReference<ClassLoader> deploy(String type, URL[] where) throws Exception {
        URLClassLoader loader = new URLClassLoader(where, null);
        Class<?> box = Class.forName(type, true, loader);
        Object kept = box.getConstructor().newInstance();
        if (((Integer) box.getMethod("value").invoke(kept)).intValue() != 42) {
            throw new AssertionError("wrong value");
        }
        ((AutoCloseable) kept).close();
        box.getConstructor().newInstance(); // forgotten: the cleaner destroys it
        return new WeakReference<ClassLoader>(loader);
    }

    // The same with callbacks (the "callbacks" case): the first call into the module gives one, and C++
    // keeps the last one until the library is unloaded.
    private static WeakReference<ClassLoader> deployCallbacks(URL[] where) throws Exception {
        URLClassLoader loader = new URLClassLoader(where, null);
        Class<?> functions = Class.forName("cbx.events.Functions", true, loader);
        Class<?> timer = Class.forName("cbx.events.Timer", true, loader);
        Class<?> callbacks = Class.forName("cbx.BridgefexCallbacks", true, loader);
        Function<Integer, Integer> twice = i -> i * 2;
        Object sum = functions.getMethod("sum", int.class, Function.class).invoke(null, 5, twice);
        if (((Long) sum).longValue() != 20L) {
            throw new AssertionError("wrong sum " + sum);
        }
        Consumer<Integer> ignore = i -> { };
        Object forgotten = timer.getConstructor(Consumer.class).newInstance(ignore);
        timer.getMethod("tick", int.class).invoke(forgotten, 3);
        forgotten = null; // the cleaner destroys it and releases its callback
        BiConsumer<Object, String> handler = (level, text) -> { };
        functions.getMethod("setLogHandler", BiConsumer.class).invoke(null, handler);
        Method registered = callbacks.getMethod("registered");
        if (((Number) registered.invoke(null)).longValue() < 1) {
            throw new AssertionError("C++ does not hold the handler");
        }
        return new WeakReference<ClassLoader>(loader); // C++ still holds the handler
    }

    private static WeakReference<ClassLoader> deploy(String mode, String type, URL[] where) throws Exception {
        return mode.equals("callbacks") ? deployCallbacks(where) : deploy(type, where);
    }

    public static void main(String[] args) throws Exception {
        URL[] where = {Redeploy.class.getProtectionDomain().getCodeSource().getLocation()};
        String mode = args[0].equals("callbacks") ? "callbacks" : "object";
        WeakReference<ClassLoader> first = deploy(mode, args[0], where);
        long deadline = System.nanoTime() + 30_000_000_000L;
        while (first.get() != null && System.nanoTime() < deadline) {
            System.gc();
            Thread.sleep(100);
        }
        if (first.get() != null) {
            throw new AssertionError("the class loader of the first deployment is kept alive");
        }
        // The library goes with its loader (the JDK unloads it with the collector): the second one
        // can load it, once that has happened.
        while (true) {
            try {
                deploy(mode, args[0], where);
                break;
            } catch (InvocationTargetException error) {
                if (System.nanoTime() > deadline
                        || !String.valueOf(error.getCause()).contains("another class loader")) {
                    throw error;
                }
                System.gc();
                Thread.sleep(100);
            }
        }
        System.out.println("redeploy OK");
    }
}
