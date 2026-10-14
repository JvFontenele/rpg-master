---
name: campanha
description: Criar (com preferências de ambientação, estilos, profundidade, ritmo e autonomia), ajustar o estilo, listar, carregar, exportar (backup) e restaurar campanhas de RPG deste projeto.
argument-hint: nova | estilo | listar | carregar <id> | backup [id] | restaurar <arquivo> [--as novo-id]
---
Subcomando: `$ARGUMENTS`

## nova
1. **Poucas perguntas, numa única mensagem** (no máximo 3, cada uma com padrão e a opção "me sugere"). Aceite linguagem natural:
   - **Premissa e ambientação** — o que EXISTE no mundo: moderna, histórica, fantasia medieval, fantasia urbana, ficção científica, cyberpunk, pós-apocalíptica, terror sobrenatural, realidade alternativa, mundo original ou híbrida.
   - **O que quer viver** — estilos de FOCO (vários): vida cotidiana, drama familiar, amizades, romance, trabalho e carreira, estudos, economia pessoal, empreendedorismo, hobbies, aventura, exploração, investigação, política, facções, sobrevivência, combate, fantasia, ficção científica, terror, construção de comunidades, sandbox livre — ou "equilibrado". Deixe claro: foco não é restrição; o que tiver causa no mundo pode acontecer.
   - **Profundidade / ritmo / autonomia** numa linha (padrão: equilibrada / equilibrado / equilibrada).
   Nome, id, sistema (`rpg.py systems`), gênero/tom em texto livre, limites de conteúdo e quem rola: proponha padrões a partir da premissa e só pergunte o que não der para inferir. Pedido de sugestão → proponha uma combinação coerente com a premissa e siga.
2. Traduza as respostas para os valores canônicos e crie: `python3 tools/rpg.py new <id> --name "<nome>" --system <sistema> --genre "<texto livre>" --tone "<texto livre>" --setting <ambientação> --styles "<a, b, c>" --depth <…> --pace <…> --autonomy <…>`. A ferramenta valida (aceita sinônimos sem acento) e recusa valores inválidos sem criar nada; se o id existir, escolha outro, nunca apague.
3. Preencha `campaign.yaml` (pc_rolls, content_limits), `world.md` e `secrets/plot.md` a partir da premissa. Use o subagente `worldbuilder` para a região inicial (1 região, 1 local inicial, 2–3 facções, 3–4 NPCs, 1 missão principal + 1 secundária). Para vida cotidiana, inclua moradia, vizinhos/família/colegas e lugares do dia a dia.
4. **Relógio com o calendário da ambientação** (só campanhas novas): moderna/realidade alternativa/histórica a partir de 2000 → `time init --calendar templates/calendars/terrestre.json --start "AAAA/M/D HH:MM"` (gregoriano com bissextos, exato a partir de 2000). Mundo fictício → peça ao `worldbuilder` um calendário JSON próprio (meses, `month_days`, semana com qualquer número de dias, `rest_days`, `holidays`) em `campaigns/<id>/assets/calendario.json` e `time init --calendar <arquivo> --start ... --today <dia da semana>`. Sem semana definida, tudo funciona; a semana pode ser acrescentada depois com `time calendar`.
5. **Economia mecânica não liga sozinha.** Se os estilos incluírem economia pessoal, trabalho e carreira ou empreendedorismo, ou a profundidade for profunda, *sugira* `economy: on` e explique: o dinheiro passa a viver só em `state/sim/resources.json`. Só mude o `campaign.yaml` se o jogador aceitar.
6. Preencha `state/current.md` com a situação inicial. `python3 tools/rpg.py check` e mostre o resultado. Não revele `secrets/`.
7. Sugira `/personagem criar`.

## estilo
Consultar: `python3 tools/rpg.py style show`. Mudar a qualquer momento, sem reiniciar nada: `rpg.py style set [--styles "..."] [--depth …] [--pace …] [--autonomy …] [--setting …] --reason "<pedido do jogador>"`. Vira evento `preferencias` no histórico (o passado não muda); repetir o mesmo valor não gera evento.

## listar
`python3 tools/rpg.py list` e mostre a tabela.

## carregar <id>
`python3 tools/rpg.py use <id>` e siga o fluxo de `/sessao retomar`.

## backup [id]
`python3 tools/rpg.py backup [id]` (padrão: campanha ativa). Mostre caminho, número de arquivos e hash.

## restaurar <arquivo> [--as novo-id]
1. `python3 tools/rpg.py restore <arquivo> [--as novo-id]`. A ferramenta recusa sobrescrever; se o id já existir, sugira `--as <id>-restaurada` e pergunte antes.
2. `python3 tools/rpg.py check --campaign <id>` e mostre o resultado.
