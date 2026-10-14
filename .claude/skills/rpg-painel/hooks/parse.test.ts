import { expect, test } from 'claude-code/testing'

import { barra, campos, conhecido, ficha, mapasJogador, missao, publico, relLinhas, rolagens } from './parse'

test('publico corta segredos do mestre e ids', () => {
  expect(publico('neutra — forasteiros. [MESTRE] informantes viram tudo')).toBe('neutra — forasteiros.')
  expect(publico('três mineiros (informantes da guilda [MESTRE]) no canto')).toBe('três mineiros no canto')
  expect(publico('cantarolando (npc-bento-quaresma, nome desconhecido)')).toBe('cantarolando')
  expect(publico('loc-breufundo — O Fole Velho')).toBe('O Fole Velho')
})

test('ficha lê recursos e inventário', () => {
  const md = `---\nname: Iara\nlevel: 1\nxp: 1\n---\n## Recursos (PV)\n- **PV:** 7 / 11 (10 + Vigor)\n- **Sortudo:** disponível\n## Defesa\n- **10** (7 + 3)\n## Inventário\nCarga: 1/9\n- Arco curto — 2d6+3\n## Dinheiro\n- 10m\n## Condições\n- Nenhuma.\n`
  const f = ficha(md)
  expect(f.nome).toBe('Iara')
  expect(f.recursos[0]).toEqual({ nome: 'PV', atual: 7, max: 11, resto: '(10 + Vigor)' })
  expect(f.linhas).toEqual(['Sortudo: disponível'])
  expect(f.inventario).toEqual([{ nome: 'Arco curto', nota: '2d6+3' }])
  expect(f.carga).toBe('Carga: 1/9')
  expect(f.dinheiro).toBe('10m')
})

test('missão oculta não aparece', () => {
  expect(missao('---\nname: X\nstatus: oculta\n---\n')).toBe(null)
  const m = missao('---\nname: X\nstatus: ativa\n---\n## Objetivos\n- [x] a\n- [ ] b\n')
  expect(m?.objetivos).toEqual([{ feito: true, texto: 'a' }, { feito: false, texto: 'b' }])
})

test('rolagens ocultas não aparecem', () => {
  const j = '{"expr":"2d6","total":9,"vs":8,"reason":"a","hidden":false}\n{"expr":"2d6","total":3,"reason":"b","hidden":true}\n'
  expect(rolagens(j, 5)).toEqual([{ expr: '2d6', total: 9, vs: 8, reason: 'a' }])
})

test('campos e barra', () => {
  expect(campos('- **Local atual:** x')).toEqual({ 'Local atual': 'x' })
  expect(barra(6, 12, 4)).toBe('██░░')
})

test('conhecido respeita oculta e seção vazia', () => {
  const md = '---\nname: Marta\nstatus: viva\n---\n## Conhecido pelos jogadores\n- Dona do bar <!-- x -->\n- algo [MESTRE] segredo\n## Aparência\n- ruiva\n'
  expect(conhecido(md)).toEqual({ nome: 'Marta', linhas: ['Dona do bar', 'algo'] })
  expect(conhecido(md.replace('viva', 'oculta'))).toBe(null)
  expect(conhecido('---\nname: X\n---\n## Conhecido pelos jogadores\n\n## Outra\n- y\n')).toBe(null)
})

test('mapasJogador lê os dados embutidos', () => {
  const html = '<script type="application/json" id="dados">{"view":"jogador","maps":[{"name":"Catacumbas","type":"masmorra","info":{"poi-a":{"name":"Entrada","kind":"entrada","status":"visitado","coord":"P11","text":[]}}}]}</script>'
  expect(mapasJogador(html)).toEqual([{ nome: 'Catacumbas', tipo: 'masmorra', lugares: [{ nome: 'Entrada', tipo: 'entrada', status: 'visitado', coord: 'P11', texto: [] }] }])
  expect(mapasJogador('<html></html>')).toEqual([])
})

test('relLinhas', () => {
  expect(relLinhas('Nenhuma relação encontrada.\n')).toEqual([])
  expect(relLinhas('rel-0001               pc-a —amigo→ npc-b [B]\n')).toEqual(['pc-a —amigo→ npc-b [B]'])
})
