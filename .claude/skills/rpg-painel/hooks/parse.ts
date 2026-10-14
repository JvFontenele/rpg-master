// Parsers puros dos arquivos da campanha. Tudo que passa por aqui vai para a tela do jogador:
// segredos do mestre são cortados em `publico`.

const ID = /\b(?:pc|npc|loc|quest|fac|item)-[a-z0-9-]+/

/** Remove o que é do mestre: parênteses com [MESTRE] ou ids, e tudo após [MESTRE] na linha. */
export const publico = (s: string): string =>
  s
    .replace(/\([^()]*\[MESTRE\][^()]*\)/g, '')
    .replace(new RegExp(`\\([^()]*${ID.source}[^()]*\\)`, 'g'), '')
    .replace(/\[MESTRE\].*$/gm, '')
    .replace(new RegExp(`^${ID.source}\\s*—\\s*`), '')
    .replace(/`/g, '')
    .replace(/\s{2,}/g, ' ')
    .trim()

/** `- **Rótulo:** valor` → { Rótulo: valor } */
export const campos = (md: string): Record<string, string> => {
  const out: Record<string, string> = {}
  for (const [, k = '', v = ''] of md.matchAll(/^- \*\*(.+?):\*\*\s*(.*)$/gm)) out[k] = v
  return out
}

/** Linhas de uma seção `## Título...` até a próxima `## `. */
export const secao = (md: string, titulo: string): string[] => {
  const ini = md.split('\n').findIndex(l => l.startsWith(`## ${titulo}`))
  if (ini < 0) return []
  const out: string[] = []
  for (const l of md.split('\n').slice(ini + 1)) {
    if (l.startsWith('## ')) break
    if (l.trim() && !l.startsWith('<!--')) out.push(l)
  }
  return out
}

export const frontmatter = (md: string): Record<string, string> => {
  const m = md.match(/^---\n([\s\S]*?)\n---/)
  const out: Record<string, string> = {}
  for (const l of m?.[1]?.split('\n') ?? []) {
    const i = l.indexOf(':')
    if (i > 0) out[l.slice(0, i).trim()] = l.slice(i + 1).trim()
  }
  return out
}

export type Ficha = {
  nome: string; nivel: string; xp: string
  recursos: { nome: string; atual: number; max: number; resto: string }[]
  linhas: string[]; defesa: string; dinheiro: string; condicoes: string[]
  carga: string; inventario: { nome: string; nota: string }[]
}

export const ficha = (md: string): Ficha => {
  const fm = frontmatter(md)
  const recursos: Ficha['recursos'] = []
  const linhas: string[] = []
  for (const l of secao(md, 'Recursos')) {
    const m = l.match(/^- \*\*(.+?):\*\*\s*(\d+)\s*\/\s*(\d+)(.*)$/)
    if (m) recursos.push({ nome: m[1] ?? '', atual: Number(m[2]), max: Number(m[3]), resto: (m[4] ?? '').trim() })
    else linhas.push(publico(l.replace(/^- /, '').replace(/\*\*/g, '')))
  }
  const bullets = (t: string) => secao(md, t).filter(l => l.startsWith('- ')).map(l => publico(l.slice(2)))
  return {
    nome: fm.name ?? '?', nivel: fm.level ?? '?', xp: fm.xp ?? '?',
    recursos, linhas,
    defesa: publico(secao(md, 'Defesa')[0]?.replace(/^- /, '').replace(/\*\*/g, '') ?? '?'),
    dinheiro: bullets('Dinheiro')[0] ?? '?',
    condicoes: bullets('Condições'),
    carga: publico(secao(md, 'Inventário').find(l => !l.startsWith('- ')) ?? ''),
    inventario: bullets('Inventário').map(l => {
      const [nome = l, ...nota] = l.split(' — ')
      return { nome, nota: nota.join(' — ') }
    }),
  }
}

export type Missao = { nome: string; status: string; objetivos: { feito: boolean; texto: string }[] }

/** null para missões ocultas: o jogador não sabe que existem. */
export const missao = (md: string): Missao | null => {
  const fm = frontmatter(md)
  if (!fm.status || fm.status === 'oculta') return null
  const objetivos = secao(md, 'Objetivos')
    .map(l => l.match(/^- \[( |x)\] (.*)$/))
    .filter((m): m is RegExpMatchArray => !!m)
    .map(m => ({ feito: m[1] === 'x', texto: publico(m[2] ?? '') }))
  return { nome: fm.name ?? '?', status: fm.status, objetivos }
}

export type Rolagem = { expr: string; total: number; vs?: number; reason: string }

export const rolagens = (jsonl: string, n: number): Rolagem[] =>
  jsonl.split('\n').filter(Boolean)
    .map(l => { try { return JSON.parse(l) } catch { return null } })
    .filter(r => r && !r.hidden)
    .slice(-n)
    .map(r => ({ expr: r.expr, total: r.total, vs: r.vs ?? undefined, reason: publico(String(r.reason ?? '')) }))

export const barra = (atual: number, max: number, largura = 12): string => {
  const cheio = max > 0 ? Math.round((Math.max(0, Math.min(atual, max)) / max) * largura) : 0
  return '█'.repeat(cheio) + '░'.repeat(largura - cheio)
}

/** '## Conhecido pelos jogadores' de um NPC/local, como world.known_section; null se oculto ou vazio. */
export const conhecido = (md: string): { nome: string; linhas: string[] } | null => {
  const fm = frontmatter(md)
  const m = md.match(/^## Conhecido pelos jogadores[^\n]*\n([\s\S]*?)(?=^## |(?![\s\S]))/m)
  const linhas = (m?.[1] ?? '').replace(/<!--[\s\S]*?-->/g, '').split('\n')
    .map(l => publico(l.replace(/^- /, ''))).filter(Boolean)
  if (fm.status === 'oculta' || linhas.length === 0) return null
  return { nome: fm.name ?? '?', linhas }
}

export type Lugar = { nome: string; tipo: string; status: string; coord: string; texto: string[] }
export type MapaJogador = { nome: string; tipo: string; lugares: Lugar[] }

/** Dados do mapa do JOGADOR (já filtrados pelo rpg.py) embutidos em maps/jogador/index.html. */
export const mapasJogador = (html: string): MapaJogador[] => {
  const m = html.match(/<script type="application\/json" id="dados">([\s\S]*?)<\/script>/)
  if (!m?.[1]) return []
  let d: { maps?: { name?: string; type?: string; info?: Record<string, { name?: string; kind?: string; status?: string; coord?: string; text?: string[] }> }[] }
  try { d = JSON.parse(m[1]) } catch { return [] }
  return (d.maps ?? []).map(mp => ({
    nome: mp.name ?? '?', tipo: mp.type ?? '',
    lugares: Object.values(mp.info ?? {}).map(i => ({
      nome: i.name ?? '?', tipo: i.kind ?? '', status: i.status ?? '', coord: i.coord ?? '', texto: i.text ?? [],
    })),
  }))
}

/** Saída de `rpg.py world rel <pc> --public`, sem a coluna de id. */
export const relLinhas = (stdout: string): string[] =>
  stdout.split('\n').map(l => l.replace(/^rel-\S+\s+/, '').trim())
    .filter(l => l && !l.startsWith('Nenhuma relação'))
