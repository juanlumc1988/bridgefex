// SPDX-License-Identifier: Apache-2.0
// The size, the alignment and the offsets that the layout of each struct class of the Java bindings
// has: one line per class, "<class> size=<n> align=<n> <OFFSET_MEMBER>=<n> ...". The classes are
// the arguments. tests/test_jni_layouts.py compares them with what a C compiler gives for a target,
// running this with the properties of the platform that the target stands for (the bindings take
// their layouts from them). Java 8: it runs on the JDK that the bindings support.

import java.lang.reflect.Field;
import java.lang.reflect.Method;

public class LayoutDump {
    public static void main(String[] names) throws Exception {
        for (String name : names) {
            Class<?> type = Class.forName(name);
            Field field;
            try {
                field = type.getDeclaredField("LAYOUT");
            } catch (NoSuchFieldException error) {
                continue; // not a struct
            }
            field.setAccessible(true);
            Object layout = field.get(null);
            Class<?> base = layout.getClass().getSuperclass();
            StringBuilder line = new StringBuilder(name);
            line.append(" size=").append(base.getMethod("byteSize").invoke(layout));
            line.append(" align=").append(base.getMethod("byteAlignment").invoke(layout));
            for (Field member : type.getDeclaredFields()) {
                if (member.getName().startsWith("OFFSET_")) {
                    member.setAccessible(true);
                    line.append(' ').append(member.getName()).append('=').append(member.get(null));
                }
            }
            System.out.println(line);
        }
    }
}
