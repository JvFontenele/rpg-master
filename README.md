# RPG Master — Claude Code como Mestre de Jogo

Ambiente local: abra o Claude Code **nesta pasta** e o Claude passa a atuar como mestre, com
campanhas persistidas em arquivos. Nada aqui altera o seu Claude global.

## Começar
```bash
cd ~/Projetos/rpg-master && claude
```
O hook de início mostra a campanha ativa. Depois:

| Quero… | Comando |
|---|---|
| Criar campanha (ambientação, estilos, profundidade, ritmo, autonomia) | `/campanha nova` |
| Ver/mudar o foco da campanha | `/campanha estilo` (`rpg.py style show\|set`) |
| Ver campanhas / trocar | `/campanha listar` · `/campanha carregar <id>` |
| Criar personagem | `/personagem criar` |
| Começar ou continuar a jogar | `/sessao retomar` (ou `/sessao iniciar`) |
| Jogar | escreva o que seu personagem faz/fala |
| Registrar um fato | `/sessao registrar <fato>` |
| Salvar no meio do jogo | `/sessao salvar` |
| Encerrar (salva + backup) | `/sessao encerrar` — depois pode fechar o terminal |
| Ficha / inventário | `/personagem ficha` · `/personagem inventario` |
| Missões, NPCs, locais, diário, rolagens, mapa, relações | `/consultar missoes\|npcs\|locais\|diario\|rolagens\|mapa\|relacoes <id>` |
| Expandir o mundo | `/mundo <pedido>` |
| Relações, linha do tempo, agenda, continuidade | `/mundo relacoes <id>\|linha-do-tempo\|agenda\|verificar` |
| Mapas | `/mapa gerar mundo\|regiao\|cidade\|masmorra\|batalha` · `/mapa listar\|abrir\|revelar\|localizar\|adicionar\|conectar\|validar\|exportar` |
| Sistemas de regras | `/sistema listar\|ver <id>\|criar <id>` |
| Backup / restaurar | `/campanha backup` · `/campanha restaurar backups/<arquivo>.tar.gz [--as novo-id]` |

Pode pedir em linguagem natural também; as skills são acionadas pelo próprio mestre.

## Como funciona
- `CLAUDE.md` — comportamento do mestre, regras de segredo, rolagens e persistência (carregado sempre).
- `.claude/skills/` — `campanha`, `sessao`, `personagem`, `consultar`, `mundo`, `sistema`, `mapa`.
- `.claude/agents/` — `worldbuilder` (cria conteúdo coerente), `continuity-reviewer` (só leitura, acha contradições), `rules-adjudicator` (só leitura, consulta regras longas).
- `.claude/settings.json` — permissões do projeto e hook `SessionStart` que roda `tools/rpg.py status --hook`.
- `tools/rpg.py` — ferramenta determinística (só biblioteca padrão do Python): dados, criação/lista de campanhas, sessões, registro de eventos, verificação de ids, backup/restauração. `python3 tools/rpg.py --help`.
- `tools/maps.py` — subcomandos `rpg.py map …`: geradores procedurais, visão do jogador filtrada, SVG, visualizador HTML, validação.
- `tools/world.py` — subcomandos `rpg.py world …`: grafo de relações, linha do tempo estruturada, agenda dos NPCs, verificador de continuidade.
- `templates/` — modelos de campanha, entidades (PJ, NPC, local, missão) e de sistema de regras.
- `systems/<id>/system.md` — sistemas de regras. Incluído: `aventura-2d6` (original, genérico).
- `campaigns/<id>/` — cada campanha (estrutura em `CLAUDE.md`). `campaigns/.active` guarda a ativa.
- `backups/` — arquivos `.tar.gz` gerados por backup e a cada `/sessao encerrar` (fora do Git).

### Rolagens
`python3 tools/rpg.py roll "2d6+2" --vs 8 --reason "Ana escala o muro"` — aceita `NdM`, `+/-`,
`kh/kl` (vantagem), `!` (explodir), `>=N` (pool de sucessos), `dF` (Fate), `d%`. Todas ficam em
`state/rolls.jsonl` da campanha (as ocultas marcadas `hidden`). `--seed` deixa a rolagem reproduzível.

### Segredos
`secrets/`, linhas `[MESTRE]` e tudo fora de "Conhecido pelos jogadores" nas fichas de NPC/local/missão
é do mestre. Como os arquivos ficam no seu disco, você pode abri-los — evite, se quiser surpresas.

### Novo sistema de regras
`/sistema criar <id>` usa `templates/system.md` (atributos, perícias, criação, testes, combate,
condições, equipamento, magia, progressão, morte/recuperação). Livros de uso pessoal vão em
`systems/<id>/reference/` (ignorado pelo Git); não redistribua material protegido.
Para D&D 5e, o SRD 5.1/5.2 é aberto (CC-BY-4.0) e pode ser cadastrado com atribuição.

### Backup e restauração
- `python3 tools/rpg.py backup [id]` → `backups/<id>-AAAAMMDD-HHMMSS.tar.gz`, relido e conferido após gravar.
- `python3 tools/rpg.py restore <arquivo> [--as novo-id]` → nunca sobrescreve uma campanha existente;
  bloqueia arquivos com caminhos perigosos.
- Para versionar: `git add -A && git commit` quando quiser (o projeto não faz commits sozinho).

## Mapas
Gerador procedural próprio, só com a biblioteca padrão do Python. Não precisa de API, rede nem instalação.

| Tipo | Comando (`python3 tools/rpg.py map gen …`) | O que gera |
|---|---|---|
| Mundo | `mundo map-x --name N [--seed --w --h --continents --land --regions --density --ruins --islands --climate --km]` | relevo por ruído fractal com máscara de continentes; mares, ilhas, lagos; rios por drenagem (*priority-flood*); biomas (planície, floresta, colinas, montanhas, picos, deserto, pântano, tundra); reinos que crescem a partir das capitais (fronteiras seguem montanhas e rios); capitais, cidades, vilas, portos, fortalezas de fronteira, ruínas (metade secretas); estradas (árvore geradora mínima + A*) com pontes e passos de montanha |
| Região | `regiao map-y --name N --parent map-x --area reg-…` (ou `--bbox`) | recorte com mais detalhe do mesmo mundo (mesmo ruído, mesmo relevo), herdando os locais **com os mesmos ids**, mais vilas novas |
| Cidade | `cidade map-z --name N [--districts --gates --river sim\|nao --taverns --temples]` | muralha, portões, vias até a praça, saídas da cidade, rio com pontes, bairros (mercado, docas, cidade alta, guarda…), ruas, construções, mercado, tavernas, templos, guildas, paço, quartel, estalagem, cais |
| Masmorra | `masmorra map-m --name N [--levels 3 --rooms 9 --traps --encounters --w --h]` | salas, corredores (árvore mínima + laços), portas, sala secreta com porta secreta, escadas alinhadas entre níveis, armadilhas, tesouros, encontros (sem estatísticas) e pontos de interesse |
| Batalha | `batalha map-b --name N --env floresta\|ruina\|interior\|caverna\|rua\|campo --party pc-a --enemies "Bandido x3" [--ambush]` ou `--from map-m --area <sala>` | grade com coordenadas (A1…), obstáculos, cobertura, terreno difícil, água, portas, posições iniciais; `map mark` marca áreas de efeito |

**Arquivos** (em `campaigns/<id>/maps/`):
- `map-*.json` é a fonte de verdade, com dados do mestre. Contém id, campanha, tipo, nome, descrição, seed e parâmetros, dimensões e escala, arquivos, áreas, locais (`poi-*`, com `ref` para `loc-*`), conexões com outros mapas, `discovery` (o que os jogadores sabem) e `updated` (data e motivo). As grades são linhas de texto, um caractere por célula: dá para editar, validar e redesenhar sem interpretar a imagem.
- `mestre/*.svg` e `mestre/index.html` mostram tudo, inclusive segredos (em roxo).
- `jogador/*.svg` e `jogador/index.html` são gerados a partir de uma **cópia filtrada** dos dados. O que não foi revelado não chega a ser escrito: nem no SVG, nem em atributos, comentários, JSON embutido ou scripts. Mapas e níveis não revelados também não aparecem.
- SVG e HTML são regenerados a cada alteração. Não edite esses arquivos.

**Visualizador:** abra `maps/jogador/index.html` (ou `mestre/`) direto no navegador, por arquivo local, sem servidor e sem CDN. Lista os mapas, tem zoom (roda do mouse ou +/−), arraste para mover e clique num local para ver descrição, status, NPCs, missões, eventos e relações, além do botão do mapa ligado. Também tem link para o SVG. `rpg.py map open` mostra os caminhos.

**Revelação gradual:** `map reveal <map> <ids> --status rumor|conhecido|visitado` (`--hide` volta atrás). Um local ligado a `loc-*` cujo "Conhecido pelos jogadores" está preenchido aparece sozinho para o jogador, pela mesma fonte de autoridade do resto do projeto.

**Outros comandos:** `map list`, `map where <id>`, `map place` (adiciona sem duplicar, ajusta para célula válida e liga vila nova à estrada), `map set`, `map connect`, `map validate` e `map render [--png]`.

**PNG (opcional):** `map render --png` usa `rsvg-convert`, `inkscape` ou Chrome/Chromium em modo headless, com perfil temporário, se algum existir. Nesta máquina há Chrome. Para não depender dele, instale por conta própria, por exemplo `sudo apt install librsvg2-bin`. Nada é instalado automaticamente, e se nenhum conversor existir a ferramenta diz "PNG NÃO gerado".

**Reprodutibilidade:** a mesma seed, com os mesmos parâmetros e a mesma versão do gerador (`generator.version`), produz o mesmo JSON. Exceções: os campos `updated` e os ids que precisaram de sufixo por já existirem na campanha.

**Limitações:**
- O visual é em grade (pixelado), não ilustrado.
- A geografia é plausível, não científica: não há placas tectônicas nem clima simulado.
- Nas regiões, rios e estradas são recalculados com mais detalhe e podem diferir um pouco do mapa-pai.
- Os nomes são gerados por sílabas.
- O mapa tático não move tokens nem decide combate.
- Não há edição interativa no navegador: as alterações passam pelos comandos.

**Ferramentas avaliadas e não integradas:**
- [Azgaar FMG](https://github.com/Azgaar/Fantasy-Map-Generator) (MIT, muito ativo) é um app de navegador. Automatizá-lo exigiria pilotar a interface num navegador headless. Dá para usá-lo manualmente e salvar a imagem em `assets/`.
- [dungeongen](https://github.com/wildfirebill/dungeongen) (MIT, beta, 1 estrela) depende de skia-python, numpy e flask e gera PNG.
- [Tiled](https://github.com/mapeditor/tiled) (GPL) é um editor gráfico, não um gerador.

Imagens próprias (retratos, tokens, mapas feitos à mão) vão em `campaigns/<id>/assets/`.

## Mundo vivo
- **Relações:** ficam em `state/relations.jsonl` e são somente acréscimo. `world rel-add A B tipo [--secret]`, `world rel-end` (o histórico é preservado) e `world rel <id> [--with npc-] [--public]`. As relações implícitas do frontmatter (`location`, `faction`, `giver`, `region`, `parent`) entram nas consultas sem serem duplicadas.
- **Linha do tempo:** fica em `state/events.jsonl`, também somente acréscimo. Use `world event-add "texto" --date "<data no mundo>" [--loc --entities --consequences --prev --hidden]`. Correções são **novos** eventos, com `--corrects evt-N --reason`. Para ver, `world timeline [--public]`. O `timeline.md` antigo continua valendo para o que veio antes.
- **Agenda dos NPCs:** seção `## Agenda` no arquivo do NPC; `world agenda` mostra objetivos, local, relações e próximas ações. É avançada só em momentos definidos (passagem de tempo, fim de sessão, evento relevante), sem simulação em segundo plano.
- **Verificador:** `world verify` é somente leitura. Detecta:
  - referências quebradas;
  - NPC em local inexistente, destruído ou diferente da cena;
  - locais duplicados;
  - missão com local removido;
  - conexões entre mapas inexistentes;
  - coordenadas fora do mapa ou em célula inválida;
  - eventos com NPC morto;
  - segredos de `secrets/` em arquivos públicos ou em `maps/jogador/`.

## Simulação do mundo (Fase 3)
O mundo evolui entre sessões **só quando o tempo fictício avança** de forma explícita. Não há processo em segundo plano, timer nem relógio real, e tudo funciona offline, sem chamar o Claude.

**Camadas:**
- **Estado** (arquivos da campanha): frontmatter dos `.md`, `state/clock.json`, `state/events.jsonl`, `state/relations.jsonl` e `state/sim/*.json`.
- **Motor** (`tools/sim.py` mais os módulos): calcula o que acontece.
- **Mestre narrativo** (Claude): narra, interpreta e propõe. Ele nunca muda o estado sem passar pelo motor.

| Módulo | Arquivo | Estado | Comandos |
|---|---|---|---|
| Núcleo: relógio, eventos, efeitos, conhecimento, recursos | `tools/sim.py` | `state/clock.json`, `state/events.jsonl`, `state/sim/{facts,resources,flags}.json` | `time`, `fact`, `res` |
| Histórico causal (ampliação da Fase 2) | `tools/world.py` | `state/events.jsonl`, `state/relations.jsonl` | `world timeline`, `world chain`, `world why`, `world verify` |
| NPCs | `tools/npc_sim.py` | `state/sim/npcs.json` (local e status continuam no frontmatter) | `npc show/knows/history/set/decide/propose/why` |
| Facções | `tools/faction_sim.py` | `state/sim/factions.json` (membros vêm do `faction:` dos NPCs) | `faction show/relations/plans/step/set/territory` |
| Economia (opcional) | `tools/economy.py` | `state/sim/economy.json` | `econ status/price/trade/set/ledger` |
| Missões | `tools/quest_engine.py` | `state/sim/quests.json` (status continua no frontmatter) | `quest define/show/list/check/set-status/objective/deadlines/history` |
| Combate | `tools/combat_engine.py` + `tools/rulesets/` | `state/sim/combat/cbt-*.json` | `combat ...` |
| Vida cotidiana (Fase 3.1) | `tools/life.py` | `state/sim/life.json` | `life add/job/course/end/attend/agenda/show` |

### Tempo
- **Calendário:** configurável (`time init --calendar cal.json`), com minutos por hora, horas por dia, dias por mês, nomes dos meses, rótulo e ano inicial. O padrão tem 12 meses de 30 dias. Internamente o tempo é um inteiro: minutos desde o início.
- **Durações:** `45min`, `6h`, `3d`, `1d6h`, `2sem`.
- **Datas:** `+3d`, `A/M/D` ou `A/M/D HH:MM`.
- **Avançar:** `time advance <duração> --op <id-único> --reason "..."`. O mesmo `--op` nunca avança duas vezes. O motor chama os geradores dos módulos (rotinas, decisões diárias, planos, prazos, economia) e processa em ordem cronológica tudo o que vencer no intervalo, inclusive o que for agendado durante o processamento.

### Eventos
- **Uma única linha do tempo**, `state/events.jsonl`, somente acréscimo. O registro de status fica em linhas `{"op":"status"}` acrescentadas depois. Os eventos antigos da Fase 2 continuam válidos e contam como `processado`.
- **Estados:** `pendente`, `processando`, `processado`, `cancelado` e `falhou`.
- **Campos:**
  - `id`, `type`, `text`;
  - `due`, que é o horário previsto; `date`, a forma legível;
  - `priority`, `cause`, `entities`, `loc`, `hidden`;
  - `conditions`, `effects`;
  - `on_unmet`: `cancelar` ou `adiar`;
  - `gen_key`, para idempotência;
  - `result` e `processed_at`, que vêm do registro de status.
- **Ordem:** `(due, priority, número do id)`, determinística, inclusive para eventos no mesmo horário.
- **Idempotência:** cada efeito é aplicado com a chave `<evt>#<i>`, registrada no mesmo arquivo e na mesma gravação atômica do próprio efeito.
- **Recuperação:** reprocessar, retomar ou repetir uma operação nunca duplica nada. Se um efeito falha, o evento vira `falhou`, o avanço fica pendente e nada parcial se repete. Corrija ou use `time cancel <evt> --reason`, depois `time resume`.
- **Condições** são verificadas antes de qualquer efeito. Não satisfeitas, cancelam ou adiam o evento, sempre com o motivo registrado.
- **Efeitos do núcleo:** `log`, `flag.set`, `entity.set` (frontmatter), `fact.create`, `knowledge.learn`, `knowledge.witness`, `knowledge.tell`, `resource.transfer`, `resource.produce`, `resource.consume`, `schedule`, `relation.add`, `relation.end` e `map.reveal` (só quando agendado explicitamente).
- **Condições do núcleo:** `all`, `any`, `not`, `flag`, `entity`, `event`, `time`, `knows`, `resource.gte` e `map.revealed`.
- Os módulos registram outros efeitos e condições.
- **Arquivos:** gravação atômica. Arquivo corrompido gera erro claro e nada é sobrescrito.

### Conhecimento
- **Fatos** (`fact add`) podem ser verdadeiros ou boatos falsos (`--false`).
- **Cada entidade** sabe, acredita ou suspeita, com fonte, instante e confiança.
- **Ninguém "sabe" algo falso:** no máximo acredita.
- **Testemunhas:** `knowledge.witness` atinge só quem está no local (pelo frontmatter `location`), não morto, e fora de viagem.
- **Relato:** `knowledge.tell` exige que quem conta conheça o fato. O ouvinte passa a acreditar, ou só a suspeitar se a confiança for baixa.
- **Condição `knows`:** as decisões de NPC usam essa condição. Um NPC sem a informação simplesmente não tem a ação disponível.

### NPCs
- **Rotina (nível 1):** determinística. Gera um evento de troca de local no início de cada bloco de horário.
- **Decisão (nível 2):**
  - Candidatas são só as ações com todas as precondições verdadeiras (conhecimento, recursos, local).
  - Escore = prioridade × utilidade − risco.
  - O desempate usa uma seed persistida.
  - O raciocínio fica registrado e pode ser consultado com `npc why`.
  - NPCs de nível ≥ 2 decidem uma vez por dia fictício.
- **Nível 3:** `npc propose`. O Claude sugere a ação e o motor valida antes de aplicar.
- **Viagens:** duração explícita, ou estimada pelo mapa a `KM_POR_DIA = 30`.

### Facções
- **Planos em etapas:** cada etapa tem condições de início, custo cobrado no início, duração, condições de conclusão e efeitos. Se as condições de conclusão falharem, o plano é interrompido, ou tenta de novo com `on_fail: retry`.
- **Posturas:** aliada, neutra, competidora, hostil, guerra, negociação ou texto livre, com placar de −100 a 100, histórico e causa. As relações antigas do grafo não são convertidas.
- **Reputação e territórios:** territórios têm controle e segurança de 0 a 100 e situação, e usam ids de `loc-*`, `reg-*` e `dist-*`.
- **Mapas:** são só representação. A simulação nunca regenera mapas nem revela locais sozinha.

### Economia (opcional)
- **Ativação:** só com `economy: on` no `campaign.yaml`. Fora disso nada roda e nenhum arquivo é criado.
- **O que faz:** produtores, consumidores e rotas por período, sem duplicar; escassez registrada em vez de saldo negativo.
- **Preço:** `base × clamp(demanda/oferta, 0,5–3)`.
- **Troca:** `econ trade` valida tudo antes, numa única gravação atômica.

### Missões
- **Estados:** `oculta` (os jogadores não sabem), `disponivel`, `descoberta`, `aceita`, `em_andamento`, `concluida`, `falhou`, `expirada` e `cancelada`. Os antigos `ativa` e `abandonada` continuam aceitos. O fluxo pode ser configurado por missão.
- **Objetivos:** só condições estruturadas concluem uma missão, nunca o texto.
- **Dependências:** bloqueiam a conclusão, e não podem formar ciclo.
- **Desbloqueio por gatilho:** a causa é o evento que disparou o desbloqueio.
- **Prazos:** a missão expira no minuto exato se o tempo avançou; concluída antes do prazo, não expira.
- **Recompensas e consequências:** aplicadas exatamente uma vez.
- **`quest history`:** mostra por que cada mudança aconteceu.

### Combate
- **Escopo:** `tools/combat_engine.py` resolve a mecânica e o Claude narra o resultado. O adaptador é escolhido pelo `system:` da campanha, a partir de `tools/rulesets/__init__.py` (`ADAPTERS`).
- **O adaptador `aventura_2d6`** implementa só o que está escrito em `systems/aventura-2d6/system.md`:
  - iniciativa 2d6+Agilidade, com empate favorecendo o PJ;
  - 1 movimento + 1 ação por turno (o chefe tem 2 ações);
  - ataque 2d6+atributo+perícia contra Defesa;
  - falha por 1–2 fica pendente da escolha "custo ou falha", sem dano antes;
  - duplo 6 é crítico (+1d6); duplo 1 é complicação;
  - dano por classe de arma e modelos de inimigo;
  - condições da §6;
  - 0 PV deixa caído + ferido grave, com teste por rodada e 3 marcadores = morte;
  - Determinação;
  - moral só sinalizada.
- **Lacunas (`GAPS`):** o que o sistema não define (cobertura, alcance, área, distância de movimento, Agilidade dos modelos de inimigo etc.) é recusado ou exige `--mod N --mod-reason` (decisão do mestre registrada no log). Nada vira regra silenciosa.
- **Estado:** fica em `state/sim/combat/cbt-*.json`.
- **Idempotência:** cada ação tem `--action-id`; repetir não aplica de novo.
- **Rolagens:** seed persistida, todas gravadas em `state/rolls.jsonl`. Com `pc_rolls: jogador`, use `--roll N`.
- **Mapa:** um mapa tático (`--map`) só é lido, para as posições iniciais.
- **Fim:** `combat end` registra o evento `combate` (se houver relógio) e imprime o que aplicar na ficha. As fichas são texto livre e não são editadas.
- **Outro sistema:** crie `tools/rulesets/<sistema>.py` com a interface documentada em `rulesets/__init__.py` e registre em `ADAPTERS`. Sem adaptador, o combate dá erro explicando isso.

### Histórico causal
Cada evento relevante carrega `cause`. Para consultar:
- `world chain <evt>` mostra as causas até a origem e as consequências.
- `world why <id>` lista o que mudou numa entidade e por quê.
- `world timeline` filtra por `--entity`, `--session`, `--from`/`--to` (tempo fictício), `--type`, `--status` e `--cause`.

O motor nunca apaga eventos: correção vira novo evento (`--corrects`), e cancelamento vira registro de status.

### Privacidade narrativa
Saídas para jogadores:
- `world timeline --public`: só o que já aconteceu e não é oculto; nada agendado, cancelado ou falho.
- `quest list/show --public`: nada de mestre, condições ou ocultas.
- `world rel --public`, `time now --public` (só a data) e `maps/jogador/`.

Eventos gerados por NPCs, facções e missões são ocultos por padrão. O estado completo do mestre (`state/sim/*`, `events.jsonl`) nunca é a fonte das saídas públicas sem filtro.

### O que é determinístico
- **Determinístico:** relógio, ordem e idempotência dos eventos, rotinas, planos, prazos, economia e validações.
- **Aleatoriedade controlada** (`random.Random(f"{seed}:{chave}")`, com a seed em `clock.json`): desempates de decisão e rolagens do combate. Mesma seed e mesmas entradas dão o mesmo resultado.
- **Depende do Claude:** narração, interpretação e as propostas de nível 3, que o motor sempre valida antes de aplicar.

### Desligar módulos
- **Por campanha:** a economia é opcional (`economy: on`). Os outros módulos não fazem nada enquanto não houver estado configurado: sem `time init` não há relógio, e sem `npc set`, `faction set` ou `quest define` não há simulação daquela entidade.
- **Globalmente**, para diagnóstico: `RPG_SIM_MODULES="npc_sim,quest_engine"` carrega só os módulos listados; `RPG_SIM_MODULES=""` desliga todos.

### Usar numa sessão normal
1. **`/sessao retomar`:** mostra data, próximos eventos e combates ativos.
2. **Durante o jogo:**
   - Ações do jogador com efeito mecânico viram eventos: `time schedule ... --effects` e `time process`, ou os comandos dos módulos.
   - Quem viu algo aprende (`knowledge.witness`), e cada consequência leva `--cause`.
3. **Viagens, descansos e esperas:** `time advance ... --op ... --reason ...`. Depois narre o que o motor processou.
4. **NPCs importantes:** proponha a ação com `npc propose`. Se o motor recusar, ela não acontece.
5. **`/sessao encerrar`:** passagem de tempo opcional e `world verify`.

## RPG universal e vida cotidiana (Fase 3.1)
Amplia o motor da Fase 3. Não há motor, relógio, linha do tempo ou grafo de relações paralelo.

**Preferências** (`campaign.yaml`, chaves `chave: valor`; listas por vírgula — o parser atual lê sem dependências):
```yaml
setting: moderna                          # o que EXISTE no mundo
styles: vida-cotidiana, romance, investigacao   # o que recebe ATENÇÃO (nunca restringe)
depth: equilibrada                        # leve | equilibrada | profunda
pace: equilibrado                         # calmo | equilibrado | dinamico | intenso | variavel
autonomy: equilibrada                     # baixa | equilibrada | alta (só narrativa)
```
- `rpg.py new ... --setting --styles --depth --pace --autonomy` valida (sinônimos sem acento aceitos; inválido não cria nada).
- `rpg.py style show|set --reason`: cada mudança real vira **um** evento `preferencias` (antes de gravar o arquivo; uma queda no meio é concluída ao repetir, sem evento duplicado). Repetir o mesmo valor não faz nada.
- **Campanhas antigas**, sem as chaves, usam padrões internos (`styles: equilibrado`, demais `equilibrad*`; ambientação = texto de `genre`), sem reescrita. `genre`/`tone` seguem como texto livre.
- O motor só lê `depth`, e só para decidir quais compromissos **rotineiros** viram lembrete. Estilos, ritmo e autonomia orientam a narração (CLAUDE.md).

**Calendário** (mesmo `state/clock.json`; campos novos opcionais):
- `month_days` (meses de tamanhos diferentes), `leap: "gregoriano"` (regra 4/100/400; único regime de bissexto suportado), `week` (qualquer número de dias), `week_offset`, `rest_days`, `holidays` (`M/D` anual ou `A/M/D`).
- Preset `templates/calendars/terrestre.json`: gregoriano com bissextos, ancorado em 1/1/2000 = sábado. Datas e dias da semana coincidem com o calendário real **de 2000 em diante** (verificado contra o `datetime` do Python). Antes de 2000, ajuste `epoch_year`/`week_offset`; antes de 1582 o calendário histórico era o juliano, **não suportado**. Feriados: nacionais fixos do Brasil (os móveis, como Carnaval e Páscoa, não estão incluídos).
- `time init ... --today <dia>` define o dia da semana inicial. `time calendar` só **acrescenta** semana, folgas e feriados a um relógio existente. Recusa trocar meses, horas ou uma semana já definida, para que nenhuma data passada seja reinterpretada. Registra um evento `calendario`.
- Recorrência única no núcleo (`sim.occurrences`): horário, dias da semana, dia do mês (31 em mês de 30 → dia 30) e "pular folgas/feriados". É determinística e idempotente pelo `gen_key` do instante. Usada pelas rotinas de NPC (blocos com `days`), pelas contas e pelos compromissos.

**Vida cotidiana** (`tools/life.py`, módulo registrado como os outros):
- Compromissos únicos ou recorrentes, para PJs ou NPCs. O gerador cria **lembretes**: nunca move o PJ nem decide nada. Com `--move-with`, os NPCs participantes livres vão ao local.
- `life attend` registra o que o **jogador declarou** (presente, atrasou, faltou) para uma ocorrência passada. É única por ocorrência, idempotente por `--op` e recuperável. Aplica `on_attend`/`on_late`/`on_miss` uma vez. Sem declaração, nada acontece.
- `life job` (expediente mais salário como conta do empregador), `life course` (aulas mais mensalidade), `life end [--reactivate]` (a conta ligada acompanha). Provas e entrevistas são `life add --kind prova|entrevista`.
- Profundidade: leve mostra só importantes, provas, entrevistas e eventos. Equilibrada acrescenta trabalho, estudo, social e saúde. Profunda acrescenta tarefas, lazer e rotina.
- **Objetivos pessoais** são missões do `quest_engine` (`type: pessoal`, `horizon`). Usam as condições `resource.gte`, `feel`, `quest` e `entity` (campo novo `home`, relação implícita `mora_em`).

**Relações pessoais** (`state/relations.jsonl`, novo registro `{"op":"feel"}`):
- Sentimentos são direcionais (quem sente → por quem). Só a variação é gravada; o valor é a soma do histórico, limitada a cada passo. Nunca há reciprocidade implícita.
- Dimensões em `sim.FEEL_DIMS`: afeto, confianca, respeito, admiracao (−100..100); intimidade, tensao, atracao (0..100). Dimensão nova = uma linha. Registros com dimensão desconhecida são preservados e somados sem faixa.
- `world feel <de> <para> <dim> <±N> --reason --op [--cause]`. Para o PJ, exige `--declared`.
- Efeito `relation.feel`, condição `feel`. `world rel-add --directed` cria rótulos não recíprocos.
- `world rel --public` nunca mostra números nem sentimentos. Os leitores antigos ignoram o registro novo.

**Economia pessoal** (só com `economy: on`; nada é ligado ou migrado sozinho):
- `state/sim/resources.json` é a única fonte do dinheiro. `world verify` acusa número na seção Dinheiro da ficha.
- Saldo inicial explícito: `econ open`. Transação avulsa: `econ pay` (valida antes, evento com origem, destino, valor, motivo e causa).
- Contas são rotas com `bill: true`. A decisão de cada ocorrência (devido, pago, faltou) e o atraso acumulado são gravados juntos, uma vez, numa só gravação. Depois vêm o pagamento, o evento de atraso e `on_arrears`, cada etapa idempotente.
- `partial: true` paga o que houver. Quitação: `econ pay --route`.
- Utilitário `sim.apply_now`: os comandos com `--op` criam e processam o evento. Repetir após uma falha conclui só o que faltou; repetir após o sucesso não reaplica nada.

**Limitações:**
- Bissexto só gregoriano.
- `ymd` percorre os anos desde a época (rápido para décadas, lento para séculos).
- Compromissos dependem da declaração do jogador para ter consequências.
- `econ set` substitui listas inteiras.
- Sentimentos não decaem com o tempo (só mudam por causas registradas).
- Feriados móveis não são calculados.

## Confiabilidade (Fase 3.1.1)
**Privacidade:**
- Uma única regra decide o que os jogadores veem de um evento (`world.is_public_event`): só o que já aconteceu e não é oculto.
  - Vale para `world timeline --public` e para o **mapa dos jogadores**, que antes mostrava eventos agendados, cancelados e falhos.
- Visibilidade padrão:
  - saldos iniciais (`econ open`) são do mestre;
  - transações (`econ pay`, `econ trade`, `econ remove`) são do mestre, a menos que envolvam um PJ;
  - compromissos, empregos e cursos de NPCs são do mestre; os do PJ são públicos.
  - `--public`/`--hidden` decidem explicitamente. A marca fica gravada no evento, não só na apresentação.
- `world rel-add --directed` nasce **secreta** (atitude interior), salvo `--public`.

**Validação antes de criar eventos** (`life add/job/course/end/attend`, `econ pay/open/remove`, `world feel`):
- Verifica entidades (empregador, escola), valores numéricos (> 0, sem traceback), colisão de id de rota e causa (`--cause`).
- Valida as consequências (`on_attend`/`on_late`/`on_miss`/`on_arrears`) com `sim.check_effects`: formato, ids citados e regras de cada tipo.
- Entrada inválida não cria nada.
- Reusar um `--op` com **outros dados** é recusado. Antes, a confirmação "já aplicado" mostrava os argumentos novos.
- `time schedule` continua aceitando referências futuras: falta de dado ali é falha **recuperável**, como na Fase 3.

**Falhas no motor:**
- **Recuperável** (dado ausente, saldo insuficiente, quem conta não sabe o fato): o evento fica `falhou` e o avanço fica pendente. A mensagem diz o procedimento:
  - corrigir o dado e repetir a operação com o mesmo `--op`, ou rodar `time resume`;
  - ou desistir com `time cancel` seguido de `time resume`.
- **Permanente** (`SimPermanent`: tipo desconhecido, valor fora da faixa, campo obrigatório ausente, política violada):
  - o evento vira `falhou` com `permanente: true` e não é retentado;
  - **não bloqueia o relógio**; o `time advance` termina com ATENÇÃO e o `world verify` lista;
  - o mestre registra a decisão com `time cancel`.
- Exceções que não são `SimError` (erros de programação) nunca viram permanentes: bloqueiam e mostram o tipo (ex.: `KeyError`).

**Economia sem perda de dados:**
- `econ set` faz **upsert por id**: cria ou atualiza campos e nunca apaga.
  - Recusa alterar `arrears`, `history` e `status`, que são gerenciados por efeitos.
  - Recusa id repetido entre tipos e o reaproveitamento de id removido.
  - Registra a mudança na linha do tempo (oculta).
- `econ remove <id> --reason --op [--forgive]`: marca `removida`, guarda histórico e atraso, e grava um evento.
  - Recusa se houver compromisso ativo usando a entrada (`life end` antes).
  - Recusa se houver atraso sem `--forgive`; com `--forgive`, o valor perdoado fica no histórico.
  - Entradas removidas não geram ciclos e não podem ser reativadas.

**Semântica de `--op`.** Nenhum comando reaplica efeitos ao repetir o `--op`. "Mesmos dados" significa os mesmos efeitos gravados e a mesma causa; um motivo diferente também conta como dado diferente.

| Comando | Repetição idêntica | Mesmo `--op`, outros dados | Depois de falha | Evidência |
|---|---|---|---|---|
| `econ pay`, `econ open`, `econ remove`, `life add/job/course/end/attend`, `world feel`, `world move` | devolve o resultado anterior ("já aplicado"), código 0 | conflito, código ≠ 0, nada muda | conclui o que faltou | `test_recovery` 5.1, 5.6; `test_life` |
| `quest set-status`, `quest objective` | "já aplicada — nada mudou", código 0 | conflito | — | `test_quests`; `test_recovery` 5.1 |
| `npc decide --commit`, `npc propose` | devolve a decisão/proposta gravada, código 0 | conflito (outra causa/proposta) | — | `test_npc_sim`; `test_recovery` 5.1 |
| `combat start --op`, `combat act --action-id` | "já criou" / "já registrada", código 0 | — | ação pendente é completada | `test_combat` 205–225 |
| `econ trade` | **recusa** ("já foi aplicada"), código ≠ 0, nada muda | conflito | retoma com o mesmo `--op` | `test_factions` 236; `test_recovery` 5.1 |
| `faction step` | **recusa** ("já foi aplicada"), nada muda; um `--op` novo responde "andamento" sem duplicar a etapa | recusa (não compara dados) | o evento falho fica para `time retry`/`resume` | `test_factions` 139–140 |
| `time advance` | **recusa** ("já foi aplicada — o tempo NÃO avançou de novo") | recusa (não compara dados) | `time resume` conclui o avanço pendente | `test_sim` |
| `time schedule --op`, `world event-add --op` (Fase 3.2, opcional) | devolve o evento original e o status atual (pendente, processado, cancelado…), código 0, nada gravado | conflito, código ≠ 0, nada muda | validação recusada não consome o `--op` | `test_recovery` 5.8 |

**`--op` em `time schedule` e `world event-add`** (opcional). A chave (`schedule:<op>` / `event:<op>`) e a assinatura (`op_sig`, sha256 do JSON canônico dos dados) ficam **na mesma linha** do evento, gravada de uma vez: a repetição é reconhecida pelo arquivo, não pela mensagem de sucesso, inclusive depois de timeout, avanço do relógio, processamento ou cancelamento.
- Entram na assinatura: `time schedule` — texto, quando, tipo, prioridade, entidades, local, causa, efeitos, condições, `--on-unmet`, `--hidden`; `world event-add` — texto, `--date`, tipo, causa, entidades, local, consequências, `--prev`, `--corrects`, `--reason`, `--session`, `--hidden`.
- **Quando** (`time schedule`): relativo guarda a **duração** — `--in 2d`, `--in 48h` e `--at +2d` são a mesma operação, e sem `--in`/`--at` vale "agora" (duração 0); repetir depois que o relógio andou devolve o evento original, sem reagendar. Absoluto (`--at A/M/D [HH:MM]`) guarda o **instante** (`2027/1/10 9:00` ≡ `2027/01/10 09:00`). Relativo e absoluto nunca são a mesma operação, mesmo que caiam no mesmo minuto.
- `world event-add` sem `--date` usa o "agora" da primeira chamada; a repetição depois de um avanço devolve o evento original.
- JSON de `--effects`/`--conditions` é comparado pelo conteúdo (ordem de chaves e espaços não importam). Listas por vírgula (`--entities`) são comparadas na ordem dada: `a,b` ≠ `b,a` (conflito explícito, nunca silencioso).
- Mesmo `<op>` em comandos diferentes não colide (cada comando tem seu prefixo).
- Limite: não há trava entre processos simultâneos (o projeto assume um único escritor por campanha).

**Comandos sem `--op`.** Repetir cria um registro novo; isso é intencional, porque cada chamada é uma operação nova:
- `time schedule` agenda outro evento (com efeitos);
- `world event-add` registra outro evento;
- `econ set` registra outra auditoria de configuração, mas não muda saldos.

Use `--op` nos comandos que o têm sempre que uma repetição for possível, por exemplo depois de um erro ou timeout.

`--cause` é validado antes de qualquer gravação em todos os comandos que o aceitam.

**Contrato do `time schedule`:**
- Definição **comprovadamente inválida** é recusada **antes de gravar** (estado idêntico). Isso inclui: formato e tipo de efeito ou condição; regras de valor (quantidade ≤ 0, `relation.feel` com dimensão ou delta inválidos ou de PJ sem `declared`, transferência para si, `resource.produce` sem `source`, campo não alterável em `entity.set`, faixa 0–100 e prefixo de território, postura consigo mesma, status de rota inválido).
- `--entities`, `--loc` e `--cause` precisam existir na hora de agendar.
- Ids dentro de `--effects`/`--conditions` podem ser criados até a execução e geram **aviso**; se ainda faltarem, a falha é **recuperável**.
- O que depende do estado do momento (saldo, transição de missão, rota removida depois, presença já registrada) é julgado na execução, como falha recuperável ou permanente.
- A falha permanente na execução continua possível para eventos gerados pelo motor e módulos, e não bloqueia o relógio.

**Relações direcionais e privacidade:**
- Desde a 3.1.1, `rel-add --directed` nasce secreta, e `--public` grava a marca `"public": true`.
- Registros direcionais anteriores, sem `secret` e sem a marca, são **ambíguos**: o registro não guarda a intenção.
- `world rel-audit` (somente leitura) lista privadas, públicas explícitas e ambíguas, e diz quais estão expostas agora em `world rel --public`, `/consultar relacoes` e no painel "Relacionados" do mapa do jogador.
- Não há migração automática. Nas campanhas reais deste projeto (`teste-validacao`, `teste-mapas` e o backup) não existe `relations.jsonl`, portanto nenhum registro é afetado.

**Eventos parcialmente aplicados:**
- Cada efeito concluído de um evento com vários efeitos grava uma linha de progresso (`processando`, `aplicados: k`) em `events.jsonl`.
- A retomada começa no primeiro efeito não aplicado. Efeitos sem chave (`flag.set`, `entity.set`) **não são reaplicados** e não sobrescrevem fatos posteriores.
- Classificação (`sim.application`): `nao_iniciado`, `parcial` ou `concluido`, com a marca *incerto* quando a falha ou a queda ocorreu dentro de um efeito composto (`econ.run`, `quest.status`, `faction.step.done`, `life.status`, `life.occur`, `life.attend` antigo) ou no meio de um efeito.
- `time inspect <evt>` mostra, efeito por efeito, o que foi aplicado, o que falta e o que é incerto, mais o histórico.
- `time retry <evt>` conclui agora um evento falho ou interrompido (recuperável). Repetir o comando com o mesmo `--op`, ou rodar `time resume` dentro de um avanço, tem o mesmo efeito.
- `time cancel` **nunca desfaz fatos**:
  - evento não iniciado: cancela normalmente;
  - evento parcial: é recusado e explica as opções. Com `--accept-partial`, encerra registrando o que foi mantido, o que não foi aplicado e o que é incerto;
  - evento concluído: é recusado; registre uma correção com `world event-add --corrects`.
- `life attend` grava a presença como efeito 0 e cada consequência como um efeito próprio, então o progresso é exato. Eventos antigos, com consequências embutidas, continuam funcionando.

**Riscos revisados:**
- **Calendário:**
  - feriado acrescentado com `time calendar` vale a partir do dia em que entrou (`since`);
  - mudar folgas guarda a regra anterior (`rest_history`);
  - ocorrências passadas não são reinterpretadas;
  - recorrências não existem antes da criação do compromisso.
- **Economia desligada com conta ligada a compromisso ativo:**
  - `world verify` avisa;
  - `life end` é recusado sem criar evento;
  - religue a economia para encerrar.
- **Local do PJ:** `world move <pc|npc> <loc> --op` registra o deslocamento declarado. As testemunhas (`knowledge.witness`) usam esse local. `world verify` acusa PJ presente na cena com outro local no frontmatter. O movimento de NPC é oculto por padrão.
- **Contrato do `time schedule`:** veja acima (definição inválida é recusada antes de gravar; referência futura gera aviso).

## Agência do protagonista e causalidade (Etapa 3.2.1)
**Guarda de agência** (`sim.agency_problem`, regra única). Efeitos que decidem pelo PJ:
- `entity.set` do `location` de um `pc-*`;
- `relation.feel` com origem `pc-*` (mesmo com `declared: true` embutido na definição);
- `quest.status` para `aceita` ou `em_andamento`;
- `resource.transfer`/`resource.consume` tirando recursos de um `pc-*`, e `econ.trade` envolvendo um `pc-*`.

**Na definição** são recusados sem gravar nada e sem consumir o `--op`: `time schedule` (inclusive dentro de um efeito `schedule`), `life add/job/course` (`--on-*`), `econ set` (`on_arrears`), `npc set`, `npc propose`, `faction set` e `quest define` (`rewards`, `consequences`, `on_deadline`, e `on_deadline.status` = `aceita`/`em_andamento`).

**Na execução** (`sim.apply`, por onde passam também os efeitos aninhados de compostos) um desses efeitos vira **falha permanente** antes de qualquer gravação: o efeito não é aplicado, o relógio não trava, o `world verify` lista e o mestre registra a decisão com `time cancel [--accept-partial]`. Isso cobre definições gravadas antes da guarda. Efeitos já aplicados do mesmo evento continuam valendo (aplicação parcial auditada, como na Fase 3.1.1).

**Exceção — só a declaração do jogador:** um efeito de **primeiro nível** de um evento marcado `"declared": true`. Só os comandos de declaração gravam a marca (`World.schedule(declared=…)`, nunca copiada do JSON do mestre, de NPC ou de facção): `world move` (PJ), `world feel --declared`, `econ pay` (PJ pagador), `econ trade` (PJ envolvido) e `quest set-status`. Eventos desses comandos gravados antes da marca são reconhecidos pelo prefixo de `gen_key` (`move:`, `feel:`, `econ:pay:`, `econ:trade:`, `quest-op:`). Consequências aninhadas (de missão, de facção, `on_arrears`) nunca herdam a exceção.

**Consequências de missão e eventos agendados pelo mestre:** o motor não tem uma representação segura de "consequência previamente autorizada pelas regras ou por ação declarada", então **ficam bloqueados** (limitação documentada). Para uma consequência que mova o PJ (ex.: prisão), o mestre narra e registra a declaração ou decisão com o comando próprio (`world move`), em vez de deixá-la automática.

**Débito automático** (rota com ou sem `bill`, consumidor) que tire recursos de um PJ exige `"authorized": "<o que o jogador autorizou>"` na entrada (texto não vazio, gravado em `economy.json`; `econ set` registra a mudança na linha do tempo). Em `life job`/`life course`, use `--authorized "..."`.
- Sem o campo, `econ set` e `life job/course` recusam a definição.
- Entrada antiga sem o campo não debita nada e não gera atraso: cada ciclo vira falha permanente, e o `world verify` aponta a entrada. Não há migração automática; autorize com `econ set` (upsert por id) ou encerre a entrada.
- Débitos entre NPCs não exigem autorização.
- Com autorização e saldo insuficiente vale a regra de atraso de sempre: `partial`, `arrears`, `on_arrears` uma vez, nada negativo e nenhuma dívida nova.

**N1 — origem do conhecimento.** Quem aprende por efeito (`knowledge.witness`, `knowledge.learn`, `knowledge.tell`) guarda o evento (`"event"` em `facts.json`). Ao decidir (decisão diária, `npc decide`, `npc propose`), o NPC grava em `sources` os eventos de origem dos `knows` **verdadeiros** usados nas precondições (dentro de `all`/`any`; nunca `not`), sem repetir e sem a própria causa. `cause` continua sendo a causa imediata (o gatilho diário ou `--cause`). `world chain` mostra as cadeias de origem (sem repetir eventos) e as consequências pela origem; `world why` mostra "origem do conhecimento". Fatos aprendidos por `fact learn` ou antes desta versão não têm evento e não geram ligação.

**N2 — expiração de missão.** O evento de prazo tem como causa a **última transição registrada** da missão (histórico em `quests.json`). Se uma transição acontece depois de o prazo já ter sido agendado (mesmo avanço), o evento pendente é cancelado com o motivo registrado e reagendado com a causa nova (`gen_key` `quest-deadline:<q>:<prazo>:<evt>`), uma única expiração. Sem transição anterior, a causa fica vazia e o texto do evento diz "sem transição anterior registrada". A contagem do prazo não mudou (continua a partir da definição).

## Auditoria final de agência, compostos e causalidade (Etapa 3.2.2)
**Guarda única** (`sim.agency_problem`, aplicada na definição e na execução, inclusive a definições antigas). Regras que dependem do estado (compromisso, conta) são registradas pelos módulos com `sim.agency_rule` e consultadas pela mesma guarda.

Classes: **1** decisão do jogador · **2** consequência legítima do mundo · **3** exige autorização explícita · **4** ambíguo (comportamento restritivo ou decisão anterior mantida; ver "Decisões pendentes").

| Efeito | Caso | Classe | Política |
|---|---|---|---|
| `entity.set` | `location` de `pc-*` | 1 | bloqueado; `world move` (3.2.1) |
| `entity.set` | `faction`, `region`, `parent`, `giver` de `pc-*` | 1 | bloqueado; entrar numa facção é escolha (registre na ficha) |
| `entity.set` | `status` de `pc-*` (ferida, presa) | 2 | permitido |
| `entity.set` | `home` de `pc-*` | 4 | permitido (decisão da 3.2.1, usada em `test_life`) |
| `relation.feel` | de `pc-*` / de NPC para `pc-*` | 1 / 2 | bloqueado (`world feel --declared`) / permitido |
| `relation.add` | com `pc-*`, rótulo ≠ `conhece` | 4 | bloqueado: o efeito cria relação **não direcional e pública**, que afirma o lado do PJ (`noiva_de`, `aliado_de`); use `world rel-add` com a declaração; a atitude do NPC vai em `relation.feel` |
| `relation.add` | `conhece` com `pc-*`; qualquer rótulo entre NPCs | 2 | permitido |
| `relation.end` | relação de `pc-*` | 2 | permitido (o outro lado pode encerrar) |
| `knowledge.tell` | `from: pc-*` | 1 | bloqueado: o que o PJ revela é escolha; registre com `fact learn <ouvinte> <fato> --source <evt>` |
| `knowledge.tell`/`learn`/`witness` | PJ ouvindo, aprendendo, testemunhando | 2 | permitido |
| `quest.status` | `aceita`/`em_andamento` / demais | 1 / 2 | bloqueado (3.2.1) / permitido |
| `resource.transfer`/`consume` | tirando de `pc-*` / dando a `pc-*`, `resource.produce` | 3 / 2 | só declarado ou `authorized` (3.2.1) / permitido |
| `econ.trade` | com `pc-*` | 1 | só `econ trade` (3.2.1) |
| `econ.run` | entrada que debita `pc-*`, fora do gerador | 3 | bloqueado: o débito autorizado vale só nas datas da entrada (antes, um ciclo extra agendado cobrava o PJ) |
| `econ.route.status` | `ativa` em rota que debita `pc-*` / `interrompida` | 3 / 2 | só `life end --reactivate` declarado / permitido |
| `life.define` | com conta que debita `pc-*` / sem conta | 3 / 2 | só `life job/course --authorized` (o `authorized` nos dados seria forjado) / permitido (lembrete) |
| `life.status` | `ativo` com conta que debita `pc-*` / `encerrado` | 3 / 2 | só `life end --reactivate` / permitido |
| `life.attend` | compromisso de `pc-*` (ou desconhecido) / de NPC | 1 / 2 | só `life attend`, e só o registro da presença / permitido |
| `allow_negative` | débito **automático** de `pc-*` | 3 | ignorado: falta de saldo vira atraso ou escassez, nunca dívida; `econ pay`/`econ trade` declarados continuam podendo usá-lo; NPCs inalterados |
| `faction.*`, `npc.*`, `life.occur`, `econ.settle`, `econ.remove`, `fact.create`, `map.reveal`, `schedule` | inclusive reputação sobre o PJ | 2 | permitido (`schedule` é contêiner: o evento agendado passa pela guarda) |
| `flag.set`, `log`, `quest.objective` | texto livre | 4 | permitido; o motor não interpreta o significado (limitação) |

**Declarações.** Gravam `"declared": true` (nunca copiado dos dados): os comandos da 3.2.1 e, quando o PJ é parte, `life job/course`, `life end` e `life attend`. Em `life attend` a marca cobre **só** o registro da presença (efeito #0); as consequências `on_*`, mesmo no primeiro nível do evento, passam pela guarda. Eventos antigos desses comandos são reconhecidos pelos prefixos `life:define:`, `life:status:` e `life:attend:`. `life.status` declarado passa a autorização **explicitamente** à conta ligada (`sim.apply(..., auth=...)`); nenhum outro efeito aninhado herda nada. O `declared` de `relation.feel` só fica gravado quando o próprio evento é a declaração.

**Compostos e recuperação parcial.** Cada sub-efeito concluído de um composto (chave `<evt>#<i>/…`) grava uma linha de progresso em `events.jsonl` (`subefeito`, `tipo`, `sub_resultado`), sem mudar a contagem de efeitos de primeiro nível. A retomada **não reaplica** sub-efeitos concluídos: antes, um `entity.set` ou `flag.set` aninhado era refeito e sobrescrevia um fato posterior (comprovado em `test_audit` B2 e B4).
- A falha registra `falhou_em` (o sub-efeito exato). Recusa da guarda **antes de gravar** não é "incerta": o evento cancela sem `--accept-partial`.
- `time inspect` mostra cada efeito (aplicado, incerto, não aplicado), os sub-efeitos concluídos, o que falhou, a autorização (`autorizacao`), a marca de declaração e as origens.
- `time cancel --accept-partial` grava também `subefeitos_concluidos` e `falhou_em`.
- O débito automático grava o texto de `authorized` que o fundamentou; `econ set` grava na auditoria o texto autorizado (`autorizacoes`).
- Limite: sub-efeitos ainda não iniciados não são listados por nome (a definição fica na missão, etapa ou conta); são os que vêm depois de `falhou_em`, na ordem da definição.

**Causalidade.** `sim.knowledge_sources` (agora no núcleo) vale para NPCs e facções:
- **Início da etapa:** o evento guarda em `sources` a origem dos `knows` verdadeiros de `start_conditions` (sem repetir a causa).
- **Conclusão:** a origem dos `knows` de `done_conditions` é gravada junto com a decisão (a retomada não reavalia) e anexada ao evento por uma linha `{"op":"sources"}` (a linha do evento não é reescrita; leitores antigos a ignoram). `world chain`/`why` seguem essas origens.
- **`fact learn --source evt-N[,evt-M]`:** evento(s) **já ocorrido(s)** (status `processado`) em que a entidade aprendeu; validado antes de gravar, várias origens preservadas (`events`), sem duplicar. Texto livre continua sendo só a fonte, sem ligação causal. Nenhum evento é criado.
- A cadeia usa o evento que **ensinou**, nunca a origem do conteúdo do fato (`origin` de `fact.create`). `fact add` registra origem `mestre`, sem ligação causal.
- Registros antigos sem `source`/`event` continuam legíveis. `world verify` acusa origem inexistente.

**Economia antiga sem `authorized`** (sem migração, sem autorização automática, sem dívida):
- A falha permanente de cada ciclo diz qual entrada e o comando exato: `econ set` (upsert por id, só com o consentimento do jogador) ou `econ remove`. Ciclos recusados não são cobrados depois.
- `world verify` dá um diagnóstico por entrada, com a contagem de ciclos recusados e o procedimento.
- `econ set` não é mais bloqueado por problema **preexistente** de outra entrada (antes, duas entradas antigas impediam autorizar qualquer uma delas). Alterar a própria entrada antiga sem autorizá-la continua recusado.
- Entrada removida deixa de ser apontada como "exige authorized"; os ciclos recusados ficam no histórico até o mestre registrá-los com `time cancel <evt> --reason` (cancelamento simples).

**Decisões pendentes** (comportamento atual mantido e coberto por teste):
- `home` de `pc-*` por consequência: permitido.
- Lista de rótulos neutros de `relation.add`: só `conhece`.
- `status` de `pc-*` aceita qualquer valor.
- Não há comando de declaração para a facção do PJ (vale a ficha).
- `flag.set`/`quest.objective` não são interpretados.

## Segurança e isolamento
- Tudo é configurado no escopo do projeto. Nenhum arquivo em `~/.claude/` foi alterado.
- Isso **não** é uma sandbox: o Claude Code continua podendo pedir acesso a outros arquivos;
  as permissões abaixo só reduzem pedidos de aprovação e bloqueiam alguns caminhos.
- Permitido sem perguntar: `python3 tools/rpg.py …`, ler `campaigns/ systems/ templates/`, editar `campaigns/`.
- Sempre pergunta: `rpg.py restore`. Negado: ler `.env*`, editar `.claude/settings*.json`, editar `backups/`.
- Ajustes pessoais vão em `.claude/settings.local.json` (ignorado pelo Git).
- Nenhum servidor MCP, nenhuma chave de API, nenhuma porta de rede aberta.

## Testes
```bash
python3 tools/test_rpg.py   # suíte completa: test_maps, test_sim, test_npc_sim, test_factions, test_quests, test_combat, test_integration, test_life, test_reliability, test_recovery, test_longrun, test_agency, test_audit
                            # + verificação por hash de teste-validacao, teste-mapas, backups/ e ~/.claude (antes × depois)
```
- **`test_audit.py`** (Etapa 3.2.2): A — recusa na definição de cada efeito protegido em todos os pontos de entrada (e dentro de `schedule`), controles positivos das consequências do mundo, definições antigas bloqueadas na execução sem travar o relógio, marcas forjadas, consequência de primeiro nível de `life attend` e aninhada de transição declarada sem herdar a declaração, comandos de declaração e `allow_negative`; B — falha injetada no meio de compostos de missão (permanente), facção, conta (recuperáveis), erro de programação e queda do processo, com retomada sem reaplicar nem sobrescrever fatos posteriores; C — origens de facção no início e na conclusão, relato, `fact learn` com origem válida, inválida, futura, múltipla e em texto livre, registro antigo sem `source`, `chain`/`why`/`verify`; D — entradas econômicas antigas sem débito, diagnóstico, correção e remoção.
- **`test_rpg.py`:** dados, campanha, sessão, log, check, backup, restauração e backup malicioso; ao final, compara hashes dos arquivos protegidos.
- **`test_agency.py`** (Etapa 3.2.1): recusa na definição de cada ponto de entrada, sem mudar o estado e sem consumir o `--op`; efeitos que não decidem pelo PJ continuam permitidos; declarações próprias com a marca `declared` persistida; marca forjada ignorada; evento antigo de `world move` compatível; consequência aninhada de transição declarada bloqueada (parcial incerto, sem nova tentativa, encerramento auditado); ação antiga de NPC parcial sem travar o relógio; matriz de autorização econômica; N1 com várias origens, relato, `chain`/`why`, persistência e conhecimento sem evento; N2 com causa da transição, reancoragem, missão sem transição, expiração única. Validado por mutação (7 mutantes, todos detectados).
- **`test_longrun.py`** (Fase 3.2): campanha longa em raiz temporária — L1 seis semanas com emprego, curso, contas, rotina de NPC, testemunhas e decisão de NPC (contagens conferidas contra um oráculo independente em `datetime`); L2 investigação encadeada (boato falso, relato, facção, missão, prazo); L3 queda, falha permanente e recuperável no meio, com o mesmo resultado final de uma execução sem falhas; L4 calendário alterado no meio; L5 privacidade e mapa do jogador; L6 determinismo (duas execuções idênticas); L7 custo de 90 dias. Sondas de agência AG1–AG7. Status `OK`/`FALHA`/`VIOLA` (o motor decidiu pelo protagonista)/`INFO` (medição ou zona cinzenta).
- **`test_recovery.py`** (Fase 3.1.1, etapas 5–6, 97 verificações):
  - conflito de `--op` e `--cause` inválido em todos os comandos;
  - falha em cada efeito de um evento de 3 efeitos, sem reaplicar efeito sem chave;
  - queda do processo simulada e efeito composto;
  - retomada repetida, cancelamento parcial com histórico só de acréscimos e evento antigo de presença;
  - calendário, economia desligada, local do PJ e testemunhas, rotina por dia da semana, condição `feel`, `on_late`, `--until`, prazo de objetivo pessoal e contrato do `time schedule`.
  - Validado por mutação: desfazer a retomada parcial, o cancelamento protegido ou o conflito de `--op` faz os testes correspondentes falharem.
- **`test_reliability.py`** (Fase 3.1.1): privacidade (timeline, rel, agenda, mapa do jogador com controle positivo), validação prévia (15 entradas inválidas sem nenhuma alteração), falha permanente × recuperável × erro de programação, `--op` reusado, `econ set`/`remove` sem perda de dados. Mostra cada verificação; `PEND` = depende de etapa não especificada.
- **`test_life.py`** (Fase 3.1):
  - preferências (vários estilos, sinônimos, inválidos, queda no meio, histórico intacto);
  - calendário (terrestre com bissextos, fictício de 3 e 5 dias, feriados, `time calendar` sem reinterpretar o passado);
  - relações direcionais;
  - emprego, curso, prova e presença declarada (com falha e retomada);
  - contas parciais, insuficientes, com atraso e consequência única, inclusive com falha no meio do avanço;
  - demissão e recontratação, profundidade, objetivos pessoais;
  - estilo secundário (mesmas causas → mesmos eventos com focos diferentes);
  - privacidade;
  - cópia da `teste-validacao` sem nenhuma alteração.
- **`test_maps.py`** (raiz temporária):
  - geração de mundo, região, cidade, masmorra de 3 níveis e batalhas;
  - reprodutibilidade por seed e validade do JSON e do SVG;
  - conectividade, com casos quebrados de propósito;
  - referências e ausência de duplicatas;
  - visão do jogador sem segredos;
  - relações, eventos, agenda e verificador (que não altera nada);
  - mapas preservados após encerrar e retomar;
  - backup e restauração com mapas;
  - campanha antiga sem mapas (cópia da `teste-validacao`);
  - PNG só declarado quando o arquivo existe;
  - nenhuma configuração global alterada.

## Integrações avaliadas (não instaladas)
| Projeto | Por que não é a base |
|---|---|
| [neuralinitiative/claude-dnd-skill](https://github.com/neuralinitiative/claude-dnd-skill) (AGPL-3.0, v2.1.4, ativo) | Melhor projeto de D&D, mas só D&D 5e, instala como plugin (`/plugin marketplace add` grava em `~/.claude/plugins`) e guarda campanhas em `~/.claude/dnd/`. Ideias aproveitadas: bloco de estado compacto para retomar, autosave por cena. |
| [mcp-tool-shop-org/claude-rpg](https://github.com/mcp-tool-shop-org/claude-rpg) (MIT, v2.0.0) | App de terminal separado que chama a API da Anthropic (exige `ANTHROPIC_API_KEY` e cobra à parte), com mundos pré-definidos e saves em `~/.claude-rpg/`. Não usa o Claude Code como mestre. |
| [owlot/claude-dnd-dungeon-master](https://github.com/owlot/claude-dnd-dungeon-master) (MIT parcial) | Arquitetura parecida com a daqui (CLAUDE.md + agentes + scripts), mas só D&D 5e, focado em ajudar um mestre humano, com TTS/site/imagens pesados. A base veio de um projeto **sem licença**. Ideias aproveitadas: scripts determinísticos, agentes com ferramentas restritas, checkpoints. |
| [jonmichaels/foundry-vtt-mcp](https://github.com/jonmichaels/foundry-vtt-mcp) (MIT, v0.8.3, fork) | Exige Foundry VTT v13/v14 (licença paga) rodando com o módulo instalado. Foundry não está instalado aqui. Pode ser adicionado depois via `.mcp.json` (ver abaixo). |

### Adicionar Foundry VTT depois (opcional)
Requer Foundry VTT licenciado + módulo "Foundry MCP Bridge". Clone e compile o servidor **dentro**
deste projeto (ex.: `vendor/foundry-vtt-mcp`, `npm install && npm run build`) e crie `.mcp.json`:
```json
{ "mcpServers": { "foundry": { "command": "node",
  "args": ["vendor/foundry-vtt-mcp/packages/mcp-server/dist/index.js"],
  "env": { "FOUNDRY_HOST": "localhost", "FOUNDRY_PORT": "31415" } } } }
```
O Claude Code pede aprovação do servidor na primeira vez. Mantenha o Foundry em `localhost`.
