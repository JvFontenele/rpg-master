export type Tab = 'ficha' | 'cena' | 'inventario' | 'missoes' | 'mapa' | 'relacoes' | 'dados'

declare module 'claude-code' {
  interface PluginState {
    'rpg-painel': { tab: Tab; tick: number }
  }
}
