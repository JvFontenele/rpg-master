---
name: consultar
description: Visão do jogador sobre missões, NPCs, locais, diário e rolagens da campanha ativa, sem revelar segredos do mestre.
argument-hint: missoes | npcs | locais | diario | rolagens | mapa | relacoes <id> | data | agenda | dinheiro
---
Consulta: `$ARGUMENTS`

Regra absoluta: mostre **somente** o que os personagens sabem — a seção "Conhecido pelos jogadores" das entidades, `state/knowledge.md` e `journal.md`. Nunca leia para o jogador `secrets/`, linhas `[MESTRE]`, ou entidades com `status: oculta`. Releia os arquivos; não use memória.

- **data**: `python3 tools/rpg.py time now --public`.
- **agenda**: `python3 tools/rpg.py life agenda --public [--span 7d]` (só compromissos não ocultos dos PJs).
- **dinheiro**: com `economy: on`, `python3 tools/rpg.py res <pc-id>`; senão, a seção Dinheiro da ficha.
- **missoes** (se houver missões estruturadas): `python3 tools/rpg.py quest list --public` e `quest show <id> --public`; senão, leia os arquivos como abaixo.
- **missoes** (arquivos): `quests/*.md` sem `status: oculta`; agrupe por ativa/concluída/falhou; mostre objetivo como os PJs o entendem, objetivos marcados, pistas e recompensa prometida.
- **npcs**: NPCs com "Conhecido pelos jogadores" não vazio: nome, onde foi visto, o que os PJs sabem, atitude percebida.
- **locais**: locais com "Conhecido pelos jogadores" não vazio e as ligações conhecidas.
- **diario**: `journal.md`, `python3 tools/rpg.py world timeline --public` e a seção "Fatos confirmados / Rumores / Hipóteses" de `state/knowledge.md`, separando claramente os três.
- **mapa**: `python3 tools/rpg.py map open` e entregue **só** o caminho de `maps/jogador/index.html` (nunca o do mestre). Se não houver mapa revelado, diga isso.
- **relacoes <id>**: `python3 tools/rpg.py world rel <id> --public` (sem números de sentimento: o jogador percebe atitudes pela narração).
- **rolagens**: últimas 15 linhas de `state/rolls.jsonl` com `"hidden": false`.
