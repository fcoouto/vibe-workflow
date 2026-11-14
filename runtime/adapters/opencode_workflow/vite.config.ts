import { fileURLToPath } from "node:url"
import { defineConfig } from "vite"
import { gui } from "@opencode/plugin/gui/vite"

export default defineConfig({ plugins: [gui({ entry: fileURLToPath(new URL("./gui.tsx", import.meta.url)),
  outFile: fileURLToPath(new URL("./dist/gui-experimental.cjs", import.meta.url)) })] })
