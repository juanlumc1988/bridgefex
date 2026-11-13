// SPDX-License-Identifier: Apache-2.0
// The runtime of the JNI bindings loaded by two class loaders in one JVM (an application server, a
// redeploy, an isolated test loader): a library can be loaded by one class loader only, and the
// second one gets the error of the JDK. The message has to say that, and not that the library is
// not found. The argument is the name of the class BridgefexRuntime of the bindings.

import java.lang.reflect.InvocationTargetException;
import java.net.URL;
import java.net.URLClassLoader;

public class TwoLoaders {
    private static void load(String name, URL[] where) throws Exception {
        URLClassLoader loader = new URLClassLoader(where, null);
        Class.forName(name, true, loader).getMethod("ensureLoaded").invoke(null);
    }

    public static void main(String[] args) throws Exception {
        URL[] where = {TwoLoaders.class.getProtectionDomain().getCodeSource().getLocation()};
        load(args[0], where);
        try {
            load(args[0], where);
        } catch (InvocationTargetException error) {
            Throwable cause = error.getCause();
            if (!(cause instanceof UnsatisfiedLinkError)) {
                throw new AssertionError("not an UnsatisfiedLinkError: " + cause, cause);
            }
            String message = cause.getMessage();
            if (!message.contains("another class loader")) {
                throw new AssertionError("it does not say why: " + message, cause);
            }
            if (message.contains("cannot find")) {
                throw new AssertionError("it says that the library was not found: " + message, cause);
            }
            System.out.println("two loaders OK: " + message);
            return;
        }
        throw new AssertionError("the second class loader loaded the library");
    }
}
