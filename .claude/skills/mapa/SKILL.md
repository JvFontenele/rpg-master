---
name: mapa
description: Mapas da campanha — gerar mundo, região, cidade, masmorra ou mapa tático de batalha; listar, abrir, revelar ao jogador, localizar/adicionar locais, conectar mapas, validar e exportar (SVG/JSON/HTML, PNG opcional).
argument-hint: gerar mundo|regiao|cidade|masmorra|batalha | listar | abrir | revelar | localizar | adicionar | conectar | validar | exportar
---
Pedido: `$ARGUMENTS`

Toda operação usa `python3 tools/rpg.py map ...` e grava arquivos de verdade em `campaigns/<id>/maps/`:
- `map-*.json` — fonte de verdade (dados do MESTRE: segredos, armadilhas, encontros).
- `mestre/` — SVGs completos + `index.html`. `jogador/` — SVGs + `index.html` gerados de uma cópia filtrada: o que não foi revelado **não existe** nesses arquivos.
Os SVG/HTML são regenerados automaticamente a cada alteração. Pergunte só o que faltar; use padrões para o resto. Nunca edite os JSON à mão sem depois rodar `map validate`.

## gerar
Escolha um id `map-<slug>` novo (a ferramenta recusa sobrescrever). Informe a seed se o jogador pedir reprodutibilidade; senão ela é sorteada e gravada.
- **mundo** (continente, reinos): `map gen mundo map-<id> --name "<nome>" [--seed N --w 160 --h 100 --continents 2 --land 0.38 --regions 6 --density 6 --ruins 4 --islands 0.4 --climate temperado|frio|quente|arido|umido --km 10]`
- **regiao** (detalhe de um reino do mundo, mesmo relevo e mesmos ids de local): `map gen regiao map-<id> --name "<nome>" --parent <map-mundo> --area <reg-id>` (ou `--bbox x0,y0,x1,y1`). Ids `reg-*` aparecem em `map list`/no JSON.
- **cidade**: `map gen cidade map-<id> --name "<nome>" [--districts 6 --gates 4 --river sim|nao --taverns 3 --temples 2]`
- **masmorra**: `map gen masmorra map-<id> --name "<nome>" [--levels 3 --rooms 9 --traps 2 --encounters 2 --w 40 --h 30]`. Encontros, tesouros e armadilhas são marcadores secretos **sem estatísticas**: defina criaturas e recompensas pelo sistema da campanha.
- **batalha** (mapa tático): `map gen batalha map-<id> --name "<nome>" --env floresta|ruina|interior|caverna|rua|campo --party pc-a,pc-b --enemies "Bandido x3, Chefe" [--ambush]` ou, para lutar numa sala de masmorra, `--from <map-masmorra> --area <id-da-sala>`. O arquivo só registra terreno e posições iniciais; movimentos e resultados seguem o motor de regras (`rpg.py roll`) e o combate em `state/current.md`. Áreas de efeito: `map mark <map> --name "..." --at C4 --radius 2`.
Depois de gerar um local importante da história, ligue-o aos dados: crie/aproveite `locations/loc-*.md` e `map set <map> <poi-id> ref=loc-...` (ou use `map place ... --ref`). Para criar o local de um mapa em outro mapa, use o mesmo `--ref` — o id é reaproveitado, sem duplicar.

## listar / abrir / exportar
- `map list` — mapas, tipos, contagens, última atualização e motivo.
- `map show <map> [--kind capital|sala|taverna|...] [--level N]` — ids, nomes, coordenadas, segredos e o que o jogador já sabe. Use isto para achar ids (não leia o JSON com scripts).
- `map open` — mostra os caminhos `file://` de `mestre/index.html` e `jogador/index.html`. Se o jogador pedir, abra no navegador local com `xdg-open <caminho>` (pede aprovação). Nada é servido na rede.
- `map render [--png]` — regenera tudo. PNG é opcional e só sai se houver rsvg-convert, inkscape ou Chrome/Chromium; reporte exatamente o que a ferramenta disser.
- "Mostre o mapa completo do mestre" → caminho de `mestre/index.html`. Nunca mostre o do mestre como se fosse do jogador.

## revelar
`map reveal <map> <ids...> --status rumor|conhecido|visitado` (ids de locais `poi-*`, áreas `reg-*`/`dist-*`/`area-*`, ou o próprio id do mapa para revelar que o mapa existe). `--all` revela tudo que não é secreto; `--hide` volta a ocultar.
- "Revele os locais que meu personagem já descobriu": leia `state/knowledge.md` e as seções "Conhecido pelos jogadores"; locais com `ref` cujo arquivo tem essa seção preenchida já aparecem sozinhos (mesma fonte de autoridade). Revele explicitamente o resto que os PJs viram ou ouviram (boato → `rumor`, viu/confirmou → `conhecido`, esteve lá → `visitado`).
- Masmorra: revele salas/corredores conforme exploram; armadilhas, portas secretas e tesouros só quando forem encontrados.
- Batalha: `map reveal <map> <map>` ao iniciar o combate; inimigos de emboscada (`--ambush`) aparecem quando revelados pelo id `tok-*`.

## localizar / adicionar
- Onde algo está: `map where <loc-id|poi-id|npc-id>`.
- Adicionar (ex.: "adicione esta ruína ao mapa"): `map place <map> --name "<nome>" --kind ruina|vila|cidade|fortaleza|ponto|... --ref loc-* (--at x,y|A1 | --near <poi-id>) [--secret] [--note "público"] [--gm-note "só mestre"]`. A ferramenta recusa duplicatas (mesmo nome ou mesmo `ref` no mapa), ajusta a posição para célula válida e liga vilas novas à rede de estradas.
- Editar: `map set <map> <elem-id> chave=valor` (name, note, gm_note, secret, ref, faction, kind, link_map, description, public_description).

## conectar
`map connect <mapa-a> <mapa-b> [--via <poi-id>] [--note "..."] [--public]` — ex.: a cidade no mapa do mundo leva ao mapa da cidade. Conexões só aparecem aos jogadores se o outro mapa também estiver revelado (ou `--public`).

## validar
`map validate [map]` — estrutura, limites, células válidas, estradas ligando assentamentos, ruas ligando estabelecimentos, salas alcançáveis e escadas alinhadas entre níveis, referências. Para continuidade completa use `/mundo verificar`.
