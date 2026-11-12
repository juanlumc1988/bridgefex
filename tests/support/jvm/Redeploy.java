// SPDX-License-Identifier: Apache-2.0
// A redeploy of an application that has the JNI bindings: its class loader is dropped and another one
// loads the bindings again, in the same JVM. The first loader has to be collected (a thread or a
// thread local of the bindings must not keep it alive), and then the library, which only one class
// loader can have, is free for the second. The arguments are the name of the class of an object of
// C++ that the bindings wrap (with a constructor without arguments, and a method value() that
// returns 42) and the library.

import java.lang.ref.WeakReference;
import java.lang.reflect.InvocationTargetException;
import java.net.URL;
import java.net.URLClassLoader;

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

    public static void main(String[] args) throws Exception {
        URL[] where = {Redeploy.class.getProtectionDomain().getCodeSource().getLocation()};
        WeakReference<ClassLoader> first = deploy(args[0], where);
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
                deploy(args[0], where);
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
