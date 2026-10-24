/// <reference types="vite/client" />

// vuedraggable@4 未附带 TypeScript 声明,此处提供最小 shim
declare module 'vuedraggable' {
  import type { DefineComponent } from 'vue'
  const Draggable: DefineComponent<Record<string, unknown>, Record<string, unknown>, unknown>
  export default Draggable
}
