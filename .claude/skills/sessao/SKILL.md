---
name: sessao
description: Ciclo de sessão de RPG — iniciar/retomar (carrega estado), registrar acontecimentos, salvar, encerrar com verificação de gravação.
argument-hint: iniciar | retomar | registrar <fato> | salvar | encerrar
---
Subcomando: `$ARGUMENTS`

## iniciar / retomar
1. `python3 tools/rpg.py status` (se não houver campanha ativa, mande usar `/campanha listar` e `/campanha carregar`).
2. Leia, nesta ordem: `campaign.yaml` (e `rpg.py style show`: foco, profundidade, ritmo e autonomia — ver CLAUDE.md), `state/current.md`, `state/knowledge.md`, as fichas em `characters/`, a última sessão em `sessions/` e o fim de `journal.md`, `secrets/plot.md`, as missões com `status: ativa`, e o arquivo do local atual. NPCs: leia só os presentes ou citados na cena.
3. Leia `systems/<system>/system.md` (seções de testes e combate no mínimo). Rode `python3 tools/rpg.py map list` (se houver mapas, use-os para distâncias e posições), `python3 tools/rpg.py world timeline` (últimos eventos) e, se houver relógio, `time now`, `time upcoming` e `life agenda --span 2d` (compromissos do PJ) (o que está para acontecer; avanço interrompido aparece aqui — resolva com `time resume` antes de jogar; eventos com falha permanente aparecem marcados e não travam o relógio — registre a decisão com `time cancel <evt> --reason ...`). `python3 tools/rpg.py combat list` mostra combates ativos para retomar.
4. `python3 tools/rpg.py session start` (se já houver sessão aberta, ela continua).
5. Se `state/current.md` tiver "Conflitos de registro pendentes", apresente-os antes de jogar.
6. Faça uma recapitulação curta (3–5 frases, sem segredos) e reabra a cena exatamente onde parou. Devolva a vez ao jogador.

## durante o jogo (vida cotidiana)
- Antes de `time advance`, veja `life agenda --span <duração>`: se um compromisso do PJ cai no intervalo, narre/pergunte ao jogador o que ele faz. O motor só lembra — nunca move o PJ nem decide presença.
- Quando o jogador **declarar** presença, atraso ou falta: `life attend <cmp> presente|atrasou|faltou --op <id> [--at "A/M/D HH:MM"] [--note ...]`. Sem declaração, não registre nada (ausência de registro não é falta).
- Quando o PJ se desloca (declarado pelo jogador), registre: `world move pc-x loc-y --op <id> --reason ...` — é o local que define o que ele testemunha.
- Emprego/curso só depois que o jogador aceitar: `life job …` / `life course …` (ver `/mundo vida`).

## registrar <fato>
`python3 tools/rpg.py log "<fato>"` (use `--hidden` se for informação do mestre). Atualize também o arquivo da entidade afetada se for o caso.

## salvar
Aplique ao disco tudo o que mudou desde o último salvamento:
1. Fichas dos PJs (PV, recursos, inventário, XP, condições; dinheiro só se `economy` estiver desligada — com `economy: on` o saldo vive em `resources.json` e muda só por transação: `econ pay`, rotas, efeitos).
2. `state/current.md` (data/hora no jogo, local, cena, relações, combate, fios abertos).
3. `state/knowledge.md` (o que os PJs aprenderam, com fonte e quem sabe).
4. NPCs, locais e missões tocados (incl. "Histórico de interações" e "Conhecido pelos jogadores").
5. Eventos importantes → `python3 tools/rpg.py world event-add ...` (ver `/mundo linha-do-tempo`); novas relações → `world rel-add`; `secrets/plot.md` para avanços ocultos. O que os PJs viram/descobriram nos mapas → `map reveal`.
6. Eventos ainda não registrados → `python3 tools/rpg.py log`.
7. `python3 tools/rpg.py check`. Releia ao menos `state/current.md` para confirmar a gravação.
Responda com uma lista curta do que foi gravado. Não interrompa a narrativa além disso.

## encerrar
1. Faça tudo de **salvar**.
2. Escreva no arquivo da sessão aberta uma seção `## Resumo` (5–10 linhas, pode conter [MESTRE]) e `## Pendências` (ganchos para a próxima).
3. Acrescente um parágrafo em `journal.md` (visão do jogador, sem segredos), começando com `### Sessão N — <data real>`.
4. Tempo: se a sessão termina com passagem de tempo explícita (ex.: "os dias até a próxima sessão"), use `time advance <duração> --op s<N>-fim --reason "..."` — NPCs, facções e missões evoluem no intervalo. Sem relógio, use o passo "agenda" de `/mundo` (só para NPCs afetados).
5. `python3 tools/rpg.py world verify` (somente leitura). Opcional: delegue ao subagente `continuity-reviewer` a revisão da sessão; se ele apontar contradições, registre-as em "Conflitos de registro pendentes" e mostre ao jogador. Não corrija em silêncio.
6. `python3 tools/rpg.py session end` — fecha a sessão, lista os arquivos gravados (com hash) e gera um backup. Mostre essa saída: ela é a confirmação de que o estado foi salvo.
7. Diga ao jogador que pode fechar o terminal e retomar com `/sessao retomar`.
