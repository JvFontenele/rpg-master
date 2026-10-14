import { atom, read, update } from 'claude-code'
import type { EngineInterface as $, Register } from 'claude-code'

import type { Tab } from '../types'
import type { Missao } from './parse'
import { barra, campos, conhecido, ficha, mapasJogador, missao, publico, relLinhas, rolagens, secao } from './parse'

const PANE = 'rpg-painel'
const TITULO = 'Campanha'
const tab = atom({ plugin: 'rpg-painel', key: 'tab' } as const, 'ficha' as Tab)
const tick = atom({ plugin: 'rpg-painel', key: 'tick' } as const, 0)
const ABAS: [Tab, string][] = [['ficha', 'Ficha'], ['cena', 'Cena'], ['inventario', 'Inventário'], ['missoes', 'Missões'], ['mapa', 'Mapa'], ['relacoes', 'Relações'], ['dados', 'Dados']]

/** Pasta da campanha ativa, ou undefined fora do projeto de RPG. */
const campanha = async ($: $) => {
  const root = await $.session.root()
  const id = (await $.fs.read(`${root}/campaigns/.active`).catch(() => '')).trim()
  return id ? `${root}/campaigns/${id}` : undefined
}

const lerOu = ($: $, path: string) => $.fs.read(path).catch(() => '')

const arquivos = async ($: $, dir: string, prefixo: string): Promise<string[]> =>
  (await $.fs.list(dir).catch(() => []))
    .filter(f => f.kind === 'file' && f.name.startsWith(prefixo) && f.name.endsWith('.md'))
    .map(f => `${dir}/${f.name}`)

const pcPath = async ($: $, dir: string) => (await arquivos($, `${dir}/characters`, 'pc-'))[0]

const atualizarStatus = async ($: $) => {
  const dir = await campanha($)
  if (!dir) return $.ui.status(undefined)
  const pc = await pcPath($, dir)
  const f = pc ? ficha(await lerOu($, pc)) : undefined
  const pv = f?.recursos.find(r => r.nome === 'PV')
  const data = publico(campos(await lerOu($, `${dir}/state/current.md`))['Data/hora no jogo'] ?? '')
  const partes = [f?.nome, pv && `PV ${pv.atual}/${pv.max}`, data.split(',').slice(0, 2).join(',')]
  return $.ui.status(`🎲 ${partes.filter(Boolean).join(' · ')}`)
}

const refrescar = async ($: $) => {
  await update($, tick, n => n + 1)
  await atualizarStatus($)
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({ name: 'painel-rpg', description: 'Abre o painel da campanha de RPG ativa' })
    if (await campanha($)) {
      void $.ui.open({ id: PANE, title: TITULO })
      await atualizarStatus($)
    }
    return next(e)
  })

  on('command.run', { command: 'painel-rpg' }, async $ => {
    await $.ui.open({ id: PANE, title: TITULO })
    await refrescar($)
    return { text: 'Painel da campanha aberto.' }
  })

  // Arquivos da campanha mudam por Write/Edit/Bash (rpg.py); redesenha depois.
  on('tool.call', async ($, e, next) => {
    const ran = await next(e)
    if (e.tool === 'Write' || e.tool === 'Edit' || e.tool === 'Bash') await refrescar($).catch(() => {})
    return ran
  })

  on('turn.complete', async ($, e, next) => {
    await refrescar($).catch(() => {})
    return next(e)
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text, Button } = $.ui.resolve(e)
    await read($, tick)
    const aba = await read($, tab)
    const dir = await campanha($)
    if (!dir) return <Text dimColor>Nenhuma campanha ativa (campaigns/.active).</Text>

    const abas = (
      <Box flexDirection="row" gap={1}>
        {ABAS.map(([id, nome]) => (
          <Button key={id} plain dimColor={aba !== id} onPress={() => update($, tab, () => id)}>{nome}</Button>
        ))}
      </Box>
    )

    let corpo
    if (aba === 'ficha') {
      const pc = await pcPath($, dir)
      if (!pc) corpo = <Text dimColor>Nenhum personagem em characters/.</Text>
      else {
        const f = ficha(await lerOu($, pc))
        corpo = (
          <Box flexDirection="column">
            <Text bold>{f.nome} <Text dimColor>· nível {f.nivel} · {f.xp} XP</Text></Text>
            {f.recursos.map(r => (
              <Text>{r.nome.padEnd(13)} <Text color={r.atual <= r.max / 3 ? 'red' : 'green'}>{barra(r.atual, r.max)}</Text> {r.atual}/{r.max}</Text>
            ))}
            {f.linhas.map(l => <Text dimColor>{l}</Text>)}
            <Text>Defesa: {f.defesa}</Text>
            <Text>Dinheiro: {f.dinheiro}</Text>
            <Text>Condições: {f.condicoes.join('; ') || 'nenhuma'}</Text>
          </Box>
        )
      }
    } else if (aba === 'inventario') {
      const pc = await pcPath($, dir)
      const f = pc ? ficha(await lerOu($, pc)) : undefined
      corpo = !f
        ? <Text dimColor>Nenhum personagem em characters/.</Text>
        : (
          <Box flexDirection="column">
            <Text bold>{f.carga || 'Inventário'}</Text>
            {f.inventario.length === 0 && <Text dimColor>Vazio.</Text>}
            {f.inventario.map(i => <Text>• {i.nome}{i.nota && <Text dimColor> — {i.nota}</Text>}</Text>)}
            <Text>Dinheiro: {f.dinheiro}</Text>
          </Box>
        )
    } else if (aba === 'cena') {
      const md = await lerOu($, `${dir}/state/current.md`)
      const c = campos(md)
      const combate = secao(md, 'Combate').map(publico).filter(Boolean)
      corpo = (
        <Box flexDirection="column">
          <Text><Text bold>Quando:</Text> {publico(c['Data/hora no jogo'] ?? '?')}</Text>
          <Text><Text bold>Onde:</Text> {publico(c['Local atual'] ?? '?')}</Text>
          <Text>{publico(c['Cena'] ?? '')}</Text>
          {combate.length > 0 && <Text bold color="red">Combate</Text>}
          {combate.map(l => <Text>{l}</Text>)}
        </Box>
      )
    } else if (aba === 'mapa') {
      // Só a visão do jogador gerada pelo rpg.py; o JSON do mestre (maps/map-*.json) nunca é lido aqui.
      const html = `${dir}/maps/jogador/index.html`
      const mapas = mapasJogador(await lerOu($, html))
      const ordem = ['visitado', 'conhecido', 'rumor']
      corpo = mapas.length === 0
        ? <Text dimColor>Nenhum mapa revelado ainda.</Text>
        : (
          <Box flexDirection="column" gap={1}>
            <Button key="abrir" plain onPress={() => { void $.process.run(['xdg-open', html]).catch(() => {}) }}>Abrir mapa no navegador</Button>
            {mapas.map(mp => (
              <Box flexDirection="column">
                <Text bold>{mp.nome} <Text dimColor>({mp.tipo})</Text></Text>
                {mp.lugares.length === 0 && <Text dimColor>Nenhum lugar conhecido.</Text>}
                {[...mp.lugares].sort((a, b) => ordem.indexOf(a.status) - ordem.indexOf(b.status)).map(l => (
                  <Text dimColor={l.status === 'rumor'}>
                    {l.status === 'visitado' ? '●' : l.status === 'rumor' ? '?' : '○'} {l.nome}
                    {l.coord && <Text dimColor> {l.coord}</Text>}
                    {l.texto.length > 0 && <Text dimColor> — {l.texto.join(' ')}</Text>}
                  </Text>
                ))}
              </Box>
            ))}
            <Text dimColor>● visitado  ○ conhecido  ? rumor</Text>
          </Box>
        )
    } else if (aba === 'relacoes') {
      const pc = await pcPath($, dir)
      const pcId = pc?.split('/').pop()?.replace(/\.md$/, '')
      const root = await $.session.root()
      // A regra do que é público é do motor: `world rel --public`.
      const rel = pcId
        ? relLinhas((await $.process.run(['python3', `${root}/tools/rpg.py`, 'world', 'rel', pcId, '--public'], { cwd: root })
          .catch(() => ({ stdout: '' }))).stdout)
        : []
      const npcs = (await Promise.all((await arquivos($, `${dir}/npcs`, 'npc-')).map(p => lerOu($, p))))
        .map(conhecido).filter((n): n is { nome: string; linhas: string[] } => n !== null)
      corpo = (
        <Box flexDirection="column" gap={1}>
          <Box flexDirection="column">
            <Text bold>Vínculos</Text>
            {rel.length === 0 && <Text dimColor>Nenhum vínculo registrado.</Text>}
            {rel.map(l => <Text>{l}</Text>)}
          </Box>
          <Box flexDirection="column">
            <Text bold>Pessoas conhecidas</Text>
            {npcs.length === 0 && <Text dimColor>Ninguém ainda.</Text>}
            {npcs.map(n => (
              <Box flexDirection="column">
                <Text>{n.nome}</Text>
                {n.linhas.map(l => <Text dimColor>  {l}</Text>)}
              </Box>
            ))}
          </Box>
        </Box>
      )
    } else if (aba === 'missoes') {
      const ms = (await Promise.all((await arquivos($, `${dir}/quests`, 'quest-')).map(p => lerOu($, p))))
        .map(missao).filter((m): m is Missao => m !== null)
      corpo = ms.length === 0
        ? <Text dimColor>Nenhuma missão conhecida.</Text>
        : (
          <Box flexDirection="column">
            {ms.map(m => (
              <Box flexDirection="column">
                <Text bold>{m.nome} <Text dimColor>({m.status})</Text></Text>
                {m.objetivos.map(o => <Text dimColor={o.feito}>{o.feito ? '☑' : '☐'} {o.texto}</Text>)}
              </Box>
            ))}
          </Box>
        )
    } else {
      const rs = rolagens(await lerOu($, `${dir}/state/rolls.jsonl`), Math.max(3, (e.viewport?.rows ?? 24) - 6))
      corpo = rs.length === 0
        ? <Text dimColor>Nenhuma rolagem ainda.</Text>
        : (
          <Box flexDirection="column">
            {rs.map(r => (
              <Text>
                <Text color={r.vs === undefined ? undefined : r.total >= r.vs ? 'green' : 'red'}>
                  {r.expr} = {r.total}{r.vs === undefined ? '' : ` vs ${r.vs}`}
                </Text>{' '}
                <Text dimColor>{r.reason}</Text>
              </Text>
            ))}
          </Box>
        )
    }

    return (
      <Box flexDirection="column" gap={1}>
        {abas}
        {corpo}
      </Box>
    )
  })
}
