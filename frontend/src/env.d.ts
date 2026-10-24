/// <reference types="vite/client" />

/** 由 vite.config.ts 从 package.json 的 version 注入(版本号唯一来源) */
declare const __APP_VERSION__: string

// vuedraggable@4 未附带 TypeScript 声明,此处提供最小 shim
declare module 'vuedraggable' {
  import type { DefineComponent } from 'vue'
  const Draggable: DefineComponent<Record<string, unknown>, Record<string, unknown>, unknown>
  export default Draggable
}
