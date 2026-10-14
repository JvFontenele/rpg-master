---
name: sistema
description: Listar, ver ou cadastrar sistemas de regras (inclusive sistemas próprios ou abertos fornecidos pelo usuário).
argument-hint: listar | ver <id> | criar <id>
---
Subcomando: `$ARGUMENTS`

- **listar**: `python3 tools/rpg.py systems`.
- **ver <id>**: resuma `systems/<id>/system.md` por seção.
- **criar <id>**:
  1. Copie `templates/system.md` para `systems/<id>/system.md` (recuse se já existir).
  2. Preencha com o jogador, seção por seção, ou a partir de um texto/arquivo que ele fornecer.
  3. Não reproduza livros protegidos por direitos autorais. Se o usuário fornecer material de uso pessoal, guarde o original em `systems/<id>/reference/` (ignorado pelo Git) e escreva no `system.md` apenas um resumo operacional com referências às páginas. Sistemas abertos (ex.: SRD 5.1 sob CC-BY-4.0) podem ser incluídos com a atribuição exigida pela licença.
  4. Garanta que as fórmulas de dados usem a notação aceita por `tools/rpg.py roll` e teste uma rolagem de exemplo com `--no-log`.
