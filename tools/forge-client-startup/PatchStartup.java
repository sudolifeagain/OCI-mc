import java.nio.file.*;
import java.util.*;
import org.objectweb.asm.*;
import org.objectweb.asm.tree.*;
import org.objectweb.asm.tree.analysis.*;

public final class PatchStartup {
    public static void main(String[] args) throws Exception {
        ClassNode node = new ClassNode();
        new ClassReader(Files.readAllBytes(Path.of(args[1]))).accept(node, 0);
        int changed = 0;
        if (args[0].equals("iaf-shadow")) {
            for (FieldNode field : node.fields) {
                if (!field.name.equals("animator")) continue;
                for (List<AnnotationNode> annotations : Arrays.asList(field.visibleAnnotations, field.invisibleAnnotations)) {
                    if (annotations == null) continue;
                    for (AnnotationNode annotation : annotations) {
                        if (!annotation.desc.equals("Lorg/spongepowered/asm/mixin/Shadow;")) continue;
                        if (annotation.values == null) annotation.values = new ArrayList<>();
                        int index = annotation.values.indexOf("remap");
                        if (index >= 0) annotation.values.set(index + 1, false);
                        else { annotation.values.add("remap"); annotation.values.add(false); }
                        changed++;
                    }
                }
            }
        } else if (args[0].equals("corgi-http")) {
            for (MethodNode method : node.methods) {
                if (!method.name.equals("fetchAnnouncementJson") || !method.desc.equals("(Ljava/lang/String;)Lcom/google/gson/JsonObject;")) continue;
                method.instructions.clear(); method.tryCatchBlocks.clear();
                if (method.localVariables != null) method.localVariables.clear();
                method.instructions.add(new VarInsnNode(Opcodes.ALOAD, 0));
                method.instructions.add(new MethodInsnNode(Opcodes.INVOKESTATIC, "corgitaco/corgilib/client/oci/AnnouncementTransport", "fetch", method.desc, false));
                method.instructions.add(new InsnNode(Opcodes.ARETURN));
                method.maxStack = 1; method.maxLocals = 1;
                changed++;
            }
        } else if (args[0].equals("bclib-air-warning")) {
            for (MethodNode method : node.methods) {
                for (AbstractInsnNode instruction : method.instructions) {
                    if (!(instruction instanceof LdcInsnNode constant) || !"ItemColors.register filtered AIR item(s) stacktrace".equals(constant.cst)) continue;
                    for (AbstractInsnNode next = instruction.getNext(); next != null; next = next.getNext()) {
                        if (next instanceof MethodInsnNode call && call.owner.equals("org/betterx/worlds/together/util/Logger") && call.name.equals("error") && call.desc.equals("(Ljava/lang/String;Ljava/lang/Exception;)V")) {
                            int slot = method.maxLocals++;
                            InsnList adapt = new InsnList();
                            adapt.add(new VarInsnNode(Opcodes.ASTORE,slot));
                            adapt.add(new InsnNode(Opcodes.ICONST_1));
                            adapt.add(new TypeInsnNode(Opcodes.ANEWARRAY,"java/lang/Object"));
                            adapt.add(new InsnNode(Opcodes.DUP));
                            adapt.add(new InsnNode(Opcodes.ICONST_0));
                            adapt.add(new VarInsnNode(Opcodes.ALOAD,slot));
                            adapt.add(new InsnNode(Opcodes.AASTORE));
                            method.instructions.insertBefore(call,adapt);
                            call.name = "warning"; call.desc = "(Ljava/lang/String;[Ljava/lang/Object;)V";
                            changed++; break;
                        }
                    }
                }
            }
        } else if (args[0].startsWith("diagnostic-")) {
            String text = args[0].equals("diagnostic-embeddium") ? "Mod mixin into Embeddium internals detected." : "Fabric API detected!";
            for (MethodNode method : node.methods) {
                for (AbstractInsnNode instruction : method.instructions) {
                    if (!(instruction instanceof LdcInsnNode constant) || !(constant.cst instanceof String message) || !message.startsWith(text)) continue;
                    for (AbstractInsnNode next = instruction.getNext(); next != null; next = next.getNext()) {
                        if (next instanceof MethodInsnNode call && (call.owner.equals("org/apache/logging/log4j/Logger") || call.owner.equals("org/slf4j/Logger")) && call.name.equals("error")) {
                            call.name = "warn"; changed++; break;
                        }
                    }
                }
            }
        } else throw new IllegalArgumentException(args[0]);
        int expected = 1;
        if (changed != expected) throw new AssertionError("Expected " + expected + " exact change(s), found " + changed);
        ClassWriter writer = new ClassWriter(ClassWriter.COMPUTE_MAXS);
        node.accept(writer);
        byte[] bytes = writer.toByteArray();
        ClassNode verify = new ClassNode(); new ClassReader(bytes).accept(verify, 0);
        for (MethodNode method : verify.methods) if ((method.access & (Opcodes.ACC_ABSTRACT | Opcodes.ACC_NATIVE)) == 0)
            new Analyzer<BasicValue>(new BasicVerifier()).analyze(verify.name, method);
        Files.write(Path.of(args[2]), bytes);
        System.out.println(args[0] + ": exact change and ASM verification passed");
    }
}
