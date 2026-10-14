"""Núcleo da simulação do mundo vivo (só stdlib, sem rede, sem processos em segundo plano).

Responsabilidades: relógio fictício, agendador + motor de eventos idempotente, registro de
efeitos/condições, conhecimento (fatos × o que cada entidade sabe/acredita/suspeita), recursos,
escrita atômica. Módulos especializados (npc_sim, faction_sim, economy, quest_engine,
combat_engine, life) registram efeitos, condições, geradores e ouvintes aqui.

Arquivos por campanha:
  state/clock.json          calendário, agora (minutos desde o início), seed, operações aplicadas
  state/events.jsonl        linha do tempo ÚNICA, append-only: eventos ocorridos, eventos agendados
                            e registros {"op":"status"} que mudam o status de um evento
  state/sim/facts.json      fatos do mundo (verdadeiros ou boatos falsos) + conhecimento por entidade
  state/sim/resources.json  saldos por entidade
  state/sim/flags.json      variáveis livres do mundo

Ordem de processamento: (due, priority, número do id). Efeitos são aplicados com chave
"<evt-id>#<índice>"; cada efeito é idempotente para sua chave (registrada no mesmo arquivo,
na mesma gravação atômica), então reprocessar/retomar nunca duplica nada.
"""
import json, os, random, re, tempfile
from pathlib import Path

from rpg import camp_dir, die, now as real_now, open_session, read_meta, sessions

STATUS = ("pendente", "processando", "processado", "cancelado", "falhou")
KNOW_RANK = {"suspeita": 1, "acredita": 2, "sabe": 3}
DEFAULT_CAL = {"minutes_per_hour": 60, "hours_per_day": 24, "days_per_month": 30,
               "months": [f"Mês {i}" for i in range(1, 13)], "year_label": "Ano", "epoch_year": 1}
MODULES = ("npc_sim", "faction_sim", "economy", "quest_engine", "combat_engine", "life")


class SimError(Exception):
    """Falha de dados/estado: RECUPERÁVEL (corrija o dado e retome, ou cancele o evento). Bloqueia o avanço."""


class SimPermanent(SimError):
    """A definição do efeito nunca poderá ser aplicada (tipo, valor ou política inválidos). O evento é marcado
    'falhou' com permanente=true, NÃO é retentado e NÃO bloqueia o relógio; o mestre registra a decisão com
    'time cancel'. Exceções que não são SimError (erros de programação) nunca viram permanentes.
    before_write=True: recusado antes de o efeito gravar qualquer coisa (o efeito, mesmo composto, não fica incerto)."""

    def __init__(self, *a, before_write=False):
        super().__init__(*a)
        self.before_write = before_write


def is_permanent(e):
    return e.get("status") == "falhou" and bool((e.get("result") or {}).get("permanente"))


RECOVER_HINT = ("corrija o dado e repita a operação com o mesmo --op, 'time retry <evt>' ou 'time resume' (se for um avanço); "
                "para desistir: 'time cancel <evt> --reason ...' (com aplicação parcial: --accept-partial) e depois 'time resume'")


# ---------------------------------------------------------------- E/S segura
def load_json(path, default):
    path = Path(path)
    if not path.exists():
        return json.loads(json.dumps(default))
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SimError(f"arquivo corrompido: {path} ({e}); nada foi alterado — restaure de backups/ ou corrija à mão")


def save_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def append_jsonl(path, rec):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def read_jsonl(path):
    path = Path(path)
    if not path.exists():
        return []
    out = []
    for n, ln in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if ln.strip():
            try:
                out.append(json.loads(ln))
            except json.JSONDecodeError:
                raise SimError(f"linha {n} corrompida em {path}; nada foi alterado")
    return out


def once(state, key):
    """True se a chave ainda não foi aplicada (e a marca). Grave `state` na mesma operação."""
    applied = state.setdefault("_applied", [])
    if key in applied:
        return False
    applied.append(key)
    return True


def set_meta(path, key, value):
    """Altera/insere 'chave: valor' no frontmatter de um .md (gravação atômica)."""
    path = Path(path)
    t = path.read_text(encoding="utf-8")
    if not t.startswith("---\n") or "\n---" not in t[3:]:
        raise SimError(f"{path.name} sem frontmatter")
    end = t.index("\n---", 3)
    head, rest = t[4:end], t[end:]
    line = f"{key}: {value}"
    if re.search(rf"^{re.escape(key)}:.*$", head, re.M):
        head = re.sub(rf"^{re.escape(key)}:.*$", line, head, count=1, flags=re.M)
    else:
        head = head + "\n" + line
    tmp = path.with_suffix(".tmp")
    tmp.write_text("---\n" + head + rest, encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------- calendário / relógio
def cal_of(clock):
    return clock.get("calendar", DEFAULT_CAL)


def _units(cal):
    mph = cal["minutes_per_hour"]
    return mph, mph * cal["hours_per_day"]


def is_leap(cal, year):
    """Único regime de bissexto suportado: 'gregoriano' (+1 dia no 2º mês). Sem a chave, nenhum ano é bissexto."""
    return cal.get("leap") == "gregoriano" and year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def month_days(cal, year):
    md = list(cal.get("month_days") or [cal["days_per_month"]] * len(cal["months"]))
    if is_leap(cal, year):
        md[1] += 1
    return md


def ymd(t, cal=DEFAULT_CAL):
    """Minutos absolutos → (ano, mês 1.., dia 1.., hora, minuto)."""
    mph, mpd = _units(cal)
    day, r = divmod(t, mpd)
    h, mi = divmod(r, mph)
    y = cal["epoch_year"]
    # ponytail: percorre os anos desde a época; campanhas de séculos ficariam lentas — usar ciclos de 400 anos se pesar
    while day >= sum(md := month_days(cal, y)):
        day -= sum(md)
        y += 1
    mo = 0
    while day >= md[mo]:
        day -= md[mo]
        mo += 1
    return y, mo + 1, day + 1, h, mi


def weekday(t, cal):
    """Nome do dia da semana em t, ou None se o calendário não tem semana."""
    wk = cal.get("week")
    return wk[(t // _units(cal)[1] + cal.get("week_offset", 0)) % len(wk)] if wk else None


def holiday(t, cal):
    """Nome do feriado em t ('M/D' anual ou 'A/M/D' único), ou None. Feriado acrescentado depois ('since') não vale
    para o passado: datas anteriores continuam interpretadas como eram."""
    y, mo, d, _, _ = ymd(t, cal)
    for h in cal.get("holidays", []):
        if h["date"] in (f"{mo}/{d}", f"{y}/{mo}/{d}") and t >= h.get("since", 0):  # since = início do dia em que entrou
            return h["name"]
    return None


def rest_days_at(t, cal):
    """Dias de folga vigentes em t: 'rest_history' guarda as regras anteriores, cada uma válida até 'until'."""
    for h in sorted(cal.get("rest_history", []), key=lambda h: h["until"]):
        if t < h["until"]:
            return h["days"]
    return cal.get("rest_days", [])


def is_rest(t, cal):
    return weekday(t, cal) in rest_days_at(t, cal) or holiday(t, cal) is not None


def fmt(t, cal=DEFAULT_CAL):
    y, mo, d, h, mi = ymd(t, cal)
    wd = weekday(t, cal)
    year = f"{cal['year_label']} {y}".strip()
    return (f"{wd}, " if wd else "") + f"Dia {d} de {cal['months'][mo - 1]}, {year}, {h:02d}:{mi:02d}"


DUR_RE = re.compile(r"(\d+)\s*(sem|d|h|min|m)", re.I)


def parse_duration(s, cal=DEFAULT_CAL):
    s = s.strip().lower().replace(" ", "")
    mph, mpd = _units(cal)
    mult = {"sem": mpd * 7, "d": mpd, "h": mph, "min": 1, "m": 1}
    pos, tot = 0, 0
    for m in DUR_RE.finditer(s):
        if m.start() != pos:
            break
        tot += int(m.group(1)) * mult[m.group(2)]
        pos = m.end()
    if pos != len(s) or not s:
        raise SimError(f"duração inválida '{s}' (ex.: 6h, 3d, 1d6h, 45min, 2sem)")
    return tot


def parse_when(s, cal, now):
    """'+3d' (relativo), 'A/M/D HH:MM' ou 'A/M/D' (absoluto), ou minutos inteiros."""
    s = s.strip()
    if s.startswith("+"):
        return now + parse_duration(s[1:], cal)
    if re.fullmatch(r"\d+", s):
        return int(s)
    m = re.fullmatch(r"(\d+)/(\d+)/(\d+)(?:\s+(\d+):(\d+))?", s)
    if not m:
        raise SimError(f"data inválida '{s}' (use +3d, A/M/D ou 'A/M/D HH:MM')")
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    h, mi = int(m.group(4) or 0), int(m.group(5) or 0)
    mph, mpd = _units(cal)
    if not (1 <= mo <= len(cal["months"]) and y >= cal["epoch_year"] and 1 <= d <= month_days(cal, y)[mo - 1]
            and h < cal["hours_per_day"] and mi < mph):
        raise SimError(f"data fora do calendário: '{s}'")
    days = sum(sum(month_days(cal, yy)) for yy in range(cal["epoch_year"], y)) + sum(month_days(cal, y)[:mo - 1]) + d - 1
    return days * mpd + h * mph + mi


def hhmm(s, cal):
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", str(s))
    if not m or int(m[1]) >= cal["hours_per_day"] or int(m[2]) >= cal["minutes_per_hour"]:
        raise SimError(f"horário inválido '{s}' (use HH:MM dentro do calendário)")
    return int(m[1]) * cal["minutes_per_hour"] + int(m[2])


def daily(t0, t1, offset, mpd):
    """Instantes dia*mpd+offset dentro de (t0, t1]."""
    d = (t0 - offset) // mpd + 1
    while d * mpd + offset <= t1:
        yield d * mpd + offset
        d += 1


def check_days(days, cal):
    """Valida uma lista de dias da semana contra o calendário (erro claro se não houver semana)."""
    if not days:
        return
    if not cal.get("week"):
        raise SimError("o calendário desta campanha não tem semana — use 'time calendar --week ... --today ...'")
    bad = [d for d in days if d not in cal["week"]]
    if bad:
        raise SimError(f"dia(s) fora da semana do calendário: {', '.join(bad)} (semana: {', '.join(cal['week'])})")


def occurrences(t0, t1, at, cal, days=None, skip_rest=False, monthday=None):
    """Recorrência determinística: instantes em (t0, t1] no horário 'at', só nos dias da semana listados
    (None = todo dia) e/ou no dia 'monthday' do mês (31 em mês de 30 → dia 30), pulando folgas/feriados
    se pedido. Mesmo intervalo → mesmos instantes; nada depende do relógio real."""
    check_days(days, cal)
    for t in daily(t0, t1, hhmm(at, cal), _units(cal)[1]):
        if days and weekday(t, cal) not in days or skip_rest and is_rest(t, cal):
            continue
        if monthday:
            y, mo, d, _, _ = ymd(t, cal)
            if d != min(int(monthday), month_days(cal, y)[mo - 1]):
                continue
        yield t


def clock_path(cdir):
    return Path(cdir) / "state" / "clock.json"


def load_clock(cdir, required=True):
    p = clock_path(cdir)
    if not p.exists():
        if required:
            raise SimError("relógio do mundo não iniciado nesta campanha — use 'rpg.py time init'")
        return None
    c = load_json(p, {})
    c.setdefault("calendar", DEFAULT_CAL)
    c.setdefault("ops", [])
    c.setdefault("seed", 0)
    return c


def validate_calendar(cal):
    def pos_int(v):
        return isinstance(v, int) and not isinstance(v, bool) and v >= 1
    for k in ("minutes_per_hour", "hours_per_day") + (() if "month_days" in cal else ("days_per_month",)):
        if not pos_int(cal.get(k)):
            raise SimError(f"calendário: '{k}' deve ser inteiro ≥ 1")
    if not cal.get("months") or not all(isinstance(m, str) for m in cal["months"]):
        raise SimError("calendário: 'months' deve ser lista de nomes")
    if "month_days" in cal and (len(cal["month_days"]) != len(cal["months"]) or not all(map(pos_int, cal["month_days"]))):
        raise SimError("calendário: 'month_days' precisa de um inteiro ≥ 1 por mês")
    if cal.get("leap") not in (None, "gregoriano") or (cal.get("leap") and len(cal["months"]) < 2):
        raise SimError("calendário: 'leap' só aceita \"gregoriano\" (e exige 2+ meses)")
    wk = cal.get("week")
    if wk is not None and (not wk or not all(isinstance(d, str) for d in wk) or len(set(wk)) != len(wk)):
        raise SimError("calendário: 'week' deve ser lista de nomes distintos")
    if cal.get("rest_days"):
        check_days(cal["rest_days"], cal)
    for h in cal.get("rest_history", []):
        if not isinstance(h, dict) or not isinstance(h.get("until"), int):
            raise SimError(f"calendário: rest_history inválido {h!r}")
        check_days(h.get("days"), cal)
    for h in cal.get("holidays", []):
        if not (isinstance(h, dict) and re.fullmatch(r"(\d+/)?\d+/\d+", str(h.get("date", ""))) and h.get("name")):
            raise SimError(f"calendário: feriado inválido {h!r} (use {{\"date\": \"M/D\" ou \"A/M/D\", \"name\": ...}})")
    cal.setdefault("year_label", "Ano")
    cal.setdefault("epoch_year", 1)
    return cal


# ---------------------------------------------------------------- contexto
class World:
    """Visão de trabalho de uma campanha durante a simulação."""

    def __init__(self, cdir, require_clock=True):
        import world
        self.dir = Path(cdir)
        self.clock = load_clock(cdir, require_clock)
        self.now = self.clock["now"] if self.clock else 0
        self.cal = cal_of(self.clock) if self.clock else DEFAULT_CAL
        self.ctx = world.Ctx(cdir)
        self.path_events = self.dir / "state" / "events.jsonl"
        self._raw = None  # cache dos registros de events.jsonl (este processo é o único escritor durante a simulação)

    def _records(self):
        if self._raw is None:
            self._raw = read_jsonl(self.path_events)
            self._gen = {r["gen_key"]: r["id"] for r in self._raw if r.get("gen_key") and "op" not in r}
            self._max = max([int(r["id"].split("-")[1]) for r in self._raw
                             if "op" not in r and re.fullmatch(r"evt-\d+", r.get("id", ""))] or [0])
        return self._raw

    def _append(self, rec):
        append_jsonl(self.path_events, rec)
        self._records().append(rec)
        if "op" not in rec:
            self._max = max(self._max, int(rec["id"].split("-")[1]))
            if rec.get("gen_key"):
                self._gen[rec["gen_key"]] = rec["id"]

    def reload(self):
        import world
        self.ctx = world.Ctx(self.dir)

    def rng(self, key):
        """Aleatoriedade controlada: mesma seed + mesma chave → mesmo resultado."""
        return random.Random(f"{self.clock['seed'] if self.clock else 0}:{key}")

    def fmt(self, t):
        return fmt(t, self.cal)

    def session(self):
        op = open_session(self.dir)
        return int(read_meta(op)["session"]) if op else (len(sessions(self.dir)) or None)

    def exists(self, eid):
        import maps
        mids, pids = maps.campaign_ids(self.dir)
        return self.ctx.exists(eid, mids, pids)

    def entity_path(self, eid):
        e = self.ctx.ent.get(eid)
        return e["path"] if e else None

    # --- eventos
    def events(self):
        return fold_events(self._records())

    def event(self, eid):
        return next((e for e in self.events() if e["id"] == eid), None)

    def next_id(self):
        self._records()
        return f"evt-{self._max + 1:04d}"

    def schedule(self, ev, cause=None, gen_key=None, declared=False):
        """Agenda um evento futuro (ou para agora). Idempotente por gen_key. declared=True só pelos comandos que registram
        uma declaração do jogador (nunca copiado de 'ev': definições do mestre, de NPCs ou de facções não o forjam)."""
        self._records()
        if gen_key and gen_key in self._gen:
            return self._gen[gen_key]
        due = ev.get("due", self.now)
        if due < self.now:
            raise SimError(f"não dá para agendar no passado ({self.fmt(due)} < {self.fmt(self.now)})")
        for c in ev.get("conditions", []):
            check_shape(c, "condition")
        for f in ev.get("effects", []):
            check_shape(f, "effect")
        rec = {"id": self.next_id(), "type": ev.get("type", "evento"), "text": ev.get("text", ""),
               "due": due, "date": self.fmt(due), "priority": int(ev.get("priority", 50)), "status": "pendente",
               "created_at": self.now, "session": self.session(), "cause": cause or ev.get("cause"),
               "entities": ev.get("entities", []), "loc": ev.get("loc"), "conditions": ev.get("conditions", []),
               "effects": ev.get("effects", []), "on_unmet": ev.get("on_unmet", "cancelar"),
               "hidden": bool(ev.get("hidden")), "gen_key": gen_key, "declared": bool(declared), "t": real_now().isoformat()}
        for k in ("consequences", "quest", "faction", "npc", "op_sig", "sources"):
            if ev.get(k):
                rec[k] = ev[k]
        self._append({k: v for k, v in rec.items() if v not in (None, "", [], False) or k in ("priority",)})
        return rec["id"]

    def set_status(self, eid, status, result=None):
        self._append({"op": "status", "id": eid, "status": status, "at": self.now,
                      "result": result, "t": real_now().isoformat()})

    def sub_record(self, eid, key):
        """Registro de conclusão do sub-efeito 'key' de um efeito composto do evento eid, ou None."""
        return next((r["result"] for r in self._records() if r.get("op") == "status" and r["id"] == eid
                     and (r.get("result") or {}).get("subefeito") == key), None)

    def sub_done(self, eid, key, typ, res):
        """Progresso ANINHADO (em events.jsonl): 'aplicados' continua sendo o índice do efeito de 1º nível em curso, então
        a contagem de efeitos concluídos não muda; 'sub_resultado' não se confunde com o 'resultado' de 1º nível."""
        self.set_status(eid, "processando", {"aplicados": int(key.split("#")[1].split("/")[0]), "subefeito": key,
                                             "tipo": typ, "sub_resultado": res})

    def add_sources(self, eid, srcs):
        """Origens causais só conhecidas na execução (ex.: conhecimento usado ao concluir a etapa de uma facção): linha
        {"op":"sources"} acrescentada — a do evento não é reescrita e os leitores antigos a ignoram. Sem repetições."""
        e = self.event(eid)
        new = [s for s in dict.fromkeys(srcs) if s not in (e.get("sources") or []) and s not in (eid, e.get("cause"))]
        if new:
            self._append({"op": "sources", "id": eid, "sources": new, "t": real_now().isoformat()})

    def record(self, text, type_="registro", **kw):
        """Evento já ocorrido (narrativo/mecânico), no instante atual do relógio."""
        gk = kw.get("gen_key")
        self._records()
        if gk and gk in self._gen:
            return self._gen[gk]
        eid = self.schedule(dict(kw, type=type_, text=text, due=self.now), cause=kw.get("cause"), gen_key=kw.get("gen_key"))
        self.set_status(eid, "processado", kw.get("result"))
        return eid


def fold_events(records):
    evs, idx = [], {}
    for r in records:
        if r.get("op") == "status":
            e = idx.get(r["id"])
            if e is not None:
                e["status"] = r["status"]
                e["result"] = r.get("result")
                e["processed_at"] = r.get("at")
                e.setdefault("history", []).append({k: r.get(k) for k in ("status", "at", "result")})
        elif r.get("op") == "sources":
            e = idx.get(r["id"])
            if e is not None:
                e["sources"] = list(dict.fromkeys((e.get("sources") or []) + r["sources"]))
        elif "op" not in r:
            e = dict(r)
            e.setdefault("status", "processado")  # eventos antigos (Fase 2) já ocorreram
            idx[e["id"]] = e
            evs.append(e)
    return evs


def _order(e):
    return (e.get("due", 0), e.get("priority", 50), int(re.sub(r"\D", "", e["id"]) or 0))


# ---------------------------------------------------------------- efeitos e condições
EFFECTS, CONDITIONS, GENERATORS, LISTENERS, VERIFIERS = {}, {}, [], [], []


def effect(name):
    def deco(f):
        EFFECTS[name] = f
        return f
    return deco


def condition(name):
    def deco(f):
        CONDITIONS[name] = f
        return f
    return deco


def generator(f):
    """f(world, t0, t1): agenda (idempotentemente, com gen_key) o que vence no intervalo."""
    GENERATORS.append(f)
    return f


def listener(f):
    """f(world, evento_processado): reage a eventos (ex.: gatilhos de missão)."""
    LISTENERS.append(f)
    return f


def verifier(f):
    """f(world) -> [problemas]: integrado ao 'world verify' (somente leitura)."""
    VERIFIERS.append(f)
    return f


def enabled_modules():
    """RPG_SIM_MODULES="npc_sim,quest_engine" limita os módulos carregados ("" = nenhum)."""
    env = os.environ.get("RPG_SIM_MODULES")
    return MODULES if env is None else tuple(m for m in env.split(",") if m.strip() in MODULES)


def load_modules():
    import importlib, importlib.util
    for m in enabled_modules():
        if importlib.util.find_spec(m):
            importlib.import_module(m)


def check_shape(obj, kind):
    if not isinstance(obj, dict) or "type" not in obj:
        raise SimError(f"{kind} precisa ser objeto com 'type': {obj!r}")
    t = obj["type"]
    if kind == "condition" and t in ("all", "any"):
        for c in obj.get("of", []):
            check_shape(c, kind)
        return
    if kind == "condition" and t == "not":
        check_shape(obj.get("of", {}), kind)
        return
    reg = CONDITIONS if kind == "condition" else EFFECTS
    if t not in reg:
        raise SimError(f"{kind} desconhecido: '{t}' (conhecidos: {', '.join(sorted(reg))})")


def check(w, cond):
    """→ (ok, motivo). Sempre com motivo legível para auditoria."""
    t = cond["type"]
    if t == "all":
        res = [check(w, c) for c in cond.get("of", [])]
        return all(r[0] for r in res), " E ".join(r[1] for r in res)
    if t == "any":
        res = [check(w, c) for c in cond.get("of", [])]
        return any(r[0] for r in res), " OU ".join(r[1] for r in res)
    if t == "not":
        ok, why = check(w, cond["of"])
        return not ok, f"NÃO({why})"
    if t not in CONDITIONS:
        raise SimError(f"condição desconhecida: {t}")
    return CONDITIONS[t](w, cond)


def apply(w, eff, key, ev, auth=None):
    """auth: autorização EXPLÍCITA passada pelo código, nunca lida dos dados do efeito: a economia passa o 'authorized'
    GRAVADO na entrada que debita o PJ; 'life.status' declarado passa a decisão do jogador para a conta ligada.
    Sub-efeito de composto (chave '<evt>#<i>/…') já concluído não é reaplicado: devolve o resultado registrado."""
    if eff["type"] not in EFFECTS:
        raise SimPermanent(f"efeito desconhecido: {eff['type']}")
    sub = "/" in key
    if sub:
        done = w.sub_record(ev["id"], key)
        if done:
            return done.get("sub_resultado")
    try:
        why = agency_problem(eff, w, ev)
        by = auth or (why and declared_effect(ev, eff) and "declaração do jogador (evento declarado)")
        if why and not by:
            raise SimPermanent(f"recusado: {why} — o motor não decide pelo protagonista", before_write=True)
        res = EFFECTS[eff["type"]](w, eff, key, ev)
    except Exception as ex:
        if not getattr(ex, "sim_key", None):
            ex.sim_key = key  # o sub-efeito mais interno que falhou (auditoria)
        raise
    if why and isinstance(res, dict):
        res = dict(res, autorizacao=by)  # o que fundamentou o efeito protegido fica no registro
    if sub:
        w.sub_done(ev["id"], key, eff["type"], res)
    return res


# ---------------------------------------------------------------- agência do protagonista
PC_CHOICE_STATES = ("aceita", "em_andamento")  # aceitar/começar uma missão é escolha do jogador
PC_WORLD_FIELDS = ("status", "home")  # entity.set em pc-*: condição imposta pelo mundo (ferido, preso; despejo, nova casa)
PC_NEUTRAL_RELS = ("conhece",)  # relation.add com pc-*: rótulo que não expressa vontade nem compromisso do PJ
# eventos anteriores à marca 'declared' (prefixos de gen_key que só os comandos de declaração geram)
DECLARED_GK = ("move:", "feel:", "econ:pay:", "econ:trade:", "quest-op:", "life:define:", "life:status:", "life:attend:")
AGENCY = {}  # tipo → regra(w, efeito, evento) → motivo | None: regras dos módulos (dependem do estado; w/evento podem faltar)


def agency_rule(name):
    def deco(f):
        AGENCY[name] = f
        return f
    return deco


def _pc(x):
    return isinstance(x, str) and x.startswith("pc-")


def agency_problem(f, w=None, ev=None):
    """Motivo se o efeito decide pelo protagonista (local, vínculos, sentimento, o que ele revela, aceitar missão, presença,
    gastar recursos dele); senão None. Só a declaração do jogador, registrada pelo comando próprio, aplica esses efeitos.
    Consequência imposta pelo mundo (ferimento, prisão, recompensa, o que NPCs sentem/sabem dele) não é bloqueada."""
    t = f.get("type")
    if t == "entity.set" and _pc(f.get("id")) and f.get("field") not in PC_WORLD_FIELDS:
        if f.get("field") == "location":
            return f"mover {f['id']} (use 'world move' com o deslocamento declarado pelo jogador)"
        return (f"mudar '{f.get('field')}' de {f['id']} (só {'/'.join(PC_WORLD_FIELDS)} mudam por consequência do mundo; "
                f"entrar numa facção é escolha do jogador: registre a declaração na ficha)")
    if t == "relation.feel" and _pc(f.get("from")):
        return f"mudar o que {f['from']} sente (use 'world feel --declared' com a declaração do jogador)"
    pc = next((x for x in (f.get("a"), f.get("b")) if _pc(x)), None) if t == "relation.add" else None
    if pc and str(f.get("rel", "")).strip().lower() not in PC_NEUTRAL_RELS:
        return (f"registrar o vínculo '{f.get('rel')}' de {pc} (só o jogador assume vínculos do protagonista: use "
                f"'world rel-add' com a declaração dele; para a atitude de um NPC, 'relation.feel' do NPC)")
    if t == "knowledge.tell" and _pc(f.get("from")):
        return (f"fazer {f['from']} contar '{f.get('fact')}' (o que o PJ revela é escolha do jogador; registre o relato "
                f"declarado com 'fact learn <ouvinte> <fato> --source <evt>')")
    if t == "quest.status" and f.get("to") in PC_CHOICE_STATES:
        return f"pôr {f.get('quest')} em '{f['to']}' (use 'quest set-status' com a decisão do jogador)"
    if t in ("resource.transfer", "resource.consume") and _pc(f.get("from") or f.get("who")):
        return f"tirar recursos de {f.get('from') or f.get('who')} (use 'econ pay' declarado ou conta com 'authorized')"
    if t == "econ.trade" and (_pc(f.get("buyer")) or _pc(f.get("seller"))):
        return "negociar em nome do protagonista (use 'econ trade' declarado)"
    return AGENCY[t](w, f, ev) if t in AGENCY else None


def agency_problems(x, what="efeitos", w=None):
    """Validação de DEFINIÇÃO: procura efeitos que decidem pelo PJ em qualquer profundidade (ex.: dentro de 'schedule')."""
    out = []
    if isinstance(x, dict):
        why = agency_problem(x, w) if "type" in x else None
        if why:
            out.append(f"{what}: {why} — o motor não decide pelo protagonista")
        for v in x.values():
            out += agency_problems(v, what, w)
    elif isinstance(x, list):
        for v in x:
            out += agency_problems(v, what, w)
    return list(dict.fromkeys(out))


def is_declared(ev):
    """O evento registra uma declaração do jogador? Marca persistida pelo próprio comando (ou, em eventos antigos, o
    prefixo de gen_key desses comandos). Vale só para os efeitos de primeiro nível do evento, nunca para consequências."""
    return bool(ev.get("declared")) or (ev.get("gen_key") or "").startswith(DECLARED_GK)


def declared_effect(ev, eff):
    """'eff' é a própria declaração registrada? Só efeito de 1º nível de evento declarado (identidade, não igualdade: a
    mesma definição repetida dentro de uma consequência não conta). Em 'life attend' só o registro da presença (#0):
    as consequências on_* que vêm depois dele no mesmo evento nunca."""
    effs = ev.get("effects") or []
    if not (is_declared(ev) and any(eff is x for x in effs)):
        return False
    return eff is effs[0] or not (ev.get("gen_key") or "").startswith("life:attend:")


# ---------------------------------------------------------------- processamento
COMPOSITE = {}  # tipo → predicado: o efeito aplica sub-efeitos/registros por dentro (falha nele = parcial INCERTO)


def composite(name, pred=lambda f: True):
    COMPOSITE[name] = pred


def is_composite(f):
    return isinstance(f, dict) and f.get("type") in COMPOSITE and bool(COMPOSITE[f["type"]](f))


def applied_count(e):
    """Quantos efeitos do evento foram aplicados POR COMPLETO, segundo os registros de status (auditável)."""
    n = 0
    for h in e.get("history", []):
        r = h.get("result") or {}
        if isinstance(r, dict):
            n = max(n, r.get("aplicados", r.get("efeito", 0) if h.get("status") == "falhou" else 0) or 0)
    return n


def application(e):
    """→ (estado, aplicados, incerto). estado: 'nao_iniciado' | 'parcial' | 'concluido' | 'cancelado'.
    incerto = o efeito seguinte pode ter sido aplicado em parte (falha/interrupção dentro de efeito composto, ou queda)."""
    effs = e.get("effects", [])
    if e["status"] == "processado":
        return "concluido", len(effs), False
    if e["status"] == "cancelado":
        r = e.get("result") or {}
        return "cancelado", len(r.get("aplicados", [])) if isinstance(r.get("aplicados"), list) else 0, bool(r.get("incerto"))
    if e["status"] == "pendente":
        return "nao_iniciado", 0, False
    n = applied_count(e)
    nxt = effs[n] if n < len(effs) else None
    if e["status"] == "falhou":  # o registro da falha decide (recusa antes de gravar = certo); sem o campo: pelo tipo
        r = e.get("result") or {}
        incerto = bool(r["incerto"]) if isinstance(r, dict) and "incerto" in r else is_composite(nxt)
    else:  # 'processando' sem registro de falha = processo interrompido no meio do efeito n
        incerto = nxt is not None
    return ("parcial" if n or incerto else "nao_iniciado"), n, incerto


def describe_application(e):
    st, n, incerto = application(e)
    effs = [f.get("type") for f in e.get("effects", [])]
    s = f"{n} de {len(effs)} efeito(s) aplicado(s)" + (f" ({', '.join(effs[:n])})" if n else "")
    if incerto and n < len(effs):
        s += f"; '{effs[n]}' pode ter sido aplicado em parte (efeito composto ou processo interrompido)"
        subs = subeffects(e, n)
        if subs:
            s += f"; sub-efeitos concluídos: {', '.join(subs)}"
        r = e.get("result") or {}
        if isinstance(r, dict) and r.get("falhou_em"):
            s += f"; falhou no sub-efeito {r['falhou_em']}"
    return s


def _failed_sub(ex, e, i):
    """{'falhou_em': chave} quando a falha foi num sub-efeito de composto (ex.: evt-0007#0/2), para a auditoria."""
    k = getattr(ex, "sim_key", None)
    return {"falhou_em": k} if k and k != f"{e['id']}#{i}" else {}


def subeffects(e, i=None):
    """Sub-efeitos de compostos CONCLUÍDOS (chave → registro), do efeito i ou de todos, segundo events.jsonl."""
    out = {}
    for h in e.get("history", []):
        r = h.get("result") or {}
        if isinstance(r, dict) and r.get("subefeito") and (i is None or r["subefeito"].startswith(f"{e['id']}#{i}/")):
            out[r["subefeito"]] = r
    return out


def process_event(w, e):
    """Processa UM evento. Retomável: começa no primeiro efeito ainda NÃO aplicado (progresso gravado em events.jsonl
    a cada efeito concluído), então efeitos já aplicados não são repetidos — nem os sem chave (flag.set, entity.set).
    Cada efeito também é idempotente pela sua chave '<evt>#<i>', o que protege o efeito em curso numa queda."""
    if e["status"] in ("processado", "cancelado"):
        return e["status"], None
    if is_permanent(e):
        return "falhou", (e.get("result") or {}).get("erro")
    w.now = max(w.now, e.get("due", w.now))
    conds = e.get("conditions", [])
    if conds and e["status"] == "pendente":  # condições só valem antes de começar a aplicar efeitos
        ok, why = check(w, {"type": "all", "of": conds})
        if not ok:
            if e.get("on_unmet") == "adiar":
                if (e.get("result") or {}).get("adiado") != why:  # evita registros repetidos idênticos
                    w.set_status(e["id"], "pendente", {"adiado": why})
                return "adiado", why
            w.set_status(e["id"], "cancelado", {"condicoes": why})
            return "cancelado", why
    effs = e.get("effects", [])
    start = min(applied_count(e), len(effs))
    results = [None] * start
    for h in e.get("history", []):  # resultados dos efeitos já aplicados (para o registro final)
        r = h.get("result") or {}
        if isinstance(r, dict) and "resultado" in r and 0 < r.get("aplicados", 0) <= start:
            results[r["aplicados"] - 1] = r["resultado"]
    if e["status"] != "processando":
        w.set_status(e["id"], "processando", {"aplicados": start} if start else None)
    i = start
    try:
        for i in range(start, len(effs)):
            res = apply(w, effs[i], f"{e['id']}#{i}", e)
            results.append(res)
            w.reload()
            if i + 1 < len(effs):  # progresso auditável; o último efeito vira o registro 'processado'
                w.set_status(e["id"], "processando", {"aplicados": i + 1, "resultado": res})
        i = len(effs)
    except SimPermanent as ex:  # nunca dará certo: registra e segue sem bloquear o relógio
        clean = ex.before_write and getattr(ex, "sim_key", None) == f"{e['id']}#{i}"  # recusa do próprio efeito, antes de gravar
        rec = {"erro": str(ex), "efeito": i, "aplicados": i, "permanente": True, "incerto": is_composite(effs[i]) and not clean,
               **_failed_sub(ex, e, i)}
        w.set_status(e["id"], "falhou", rec)
        e["status"], e["result"] = "falhou", rec
        e.setdefault("history", []).append({"status": "falhou", "at": w.now, "result": rec})
        part = f" — aplicação PARCIAL: {describe_application(e)}" if application(e)[0] == "parcial" else ""
        return "falhou", f"FALHA PERMANENTE no efeito {i}: {ex}{part}"
    except Exception as ex:  # recuperável (ou bug): registra; a retomada começa no efeito que falhou
        kind = "" if isinstance(ex, SimError) else f"{type(ex).__name__}: "  # erro de programação: tipo visível
        rec = {"erro": f"{type(ex).__name__}: {ex}", "efeito": i, "aplicados": i,
               "incerto": is_composite(effs[i] if i < len(effs) else None), **_failed_sub(ex, e, i)}
        w.set_status(e["id"], "falhou", rec)
        e2 = dict(e, status="falhou", result=rec, history=e.get("history", []) + [{"status": "falhou", "result": rec}])
        part = f" Aplicação PARCIAL: {describe_application(e2)}." if application(e2)[0] == "parcial" else ""
        raise SimError(f"{e['id']} falhou no efeito {i}: {kind}{ex}.{part} — {RECOVER_HINT}")
    w.set_status(e["id"], "processado", {"efeitos": results} if results else None)
    e["status"] = "processado"
    for f in LISTENERS:
        f(w, e)
    return "processado", results


def run_until(w, t1, log):
    """Processa, em ordem, tudo que vence até t1 (inclusive o que for agendado no caminho)."""
    for g in GENERATORS:
        g(w, w.now, t1)
    deferred = set()
    while True:
        due = sorted((e for e in w.events() if e["status"] in ("pendente", "processando", "falhou") and not is_permanent(e)
                      and e.get("due", 0) <= t1 and e["id"] not in deferred), key=_order)
        if not due:
            break
        e = due[0]
        st, info = process_event(w, e)
        if st == "adiado":
            deferred.add(e["id"])
        elif st == "processado":
            deferred.clear()  # o mundo mudou: condições adiadas podem ter ficado verdadeiras
        log.append((e, st, info))
    w.now = t1


def advance(cdir, minutes, op, reason):
    load_modules()
    w = World(cdir)
    c = w.clock
    if minutes <= 0:
        raise SimError("a duração precisa ser positiva")
    if op in c["ops"]:
        raise SimError(f"operação '{op}' já foi aplicada — o tempo NÃO avançou de novo")
    pend = c.get("pending_op")
    if pend and pend["op"] != op:
        raise SimError(f"há um avanço interrompido ('{pend['op']}'); resolva com 'time resume' antes")
    if not pend:
        pend = {"op": op, "from": w.now, "to": w.now + minutes, "reason": reason}
        c["pending_op"] = pend
        save_json(clock_path(cdir), c)
    return _finish(cdir, w, pend)


def resume(cdir):
    load_modules()
    w = World(cdir)
    pend = w.clock.get("pending_op")
    if not pend:
        raise SimError("nenhum avanço pendente")
    return _finish(cdir, w, pend)


def _finish(cdir, w, pend):
    log = []
    run_until(w, pend["to"], log)  # falha → SimError; o avanço fica pendente para 'time resume'
    c = load_clock(cdir)
    c["now"] = pend["to"]
    c["ops"].append(pend["op"])
    c.pop("pending_op", None)
    save_json(clock_path(cdir), c)
    w.clock = c
    w.record(f"Tempo avançou: {pend['reason']}", "tempo", gen_key=f"op:{pend['op']}", hidden=True,
             result={"de": pend["from"], "para": pend["to"], "op": pend["op"]})
    return w, log


def apply_now(w, spec, gen_key, declared=False):
    """Evento para AGORA, idempotente por gen_key, processado em seguida. Repetir a mesma operação após uma falha
    conclui só o que faltou; repetir após o sucesso não reaplica nada. → (evento, já_estava_processado)."""
    w._records()
    old = w._gen.get(gen_key)
    if old:  # mesma operação: só vale repetir com os MESMOS dados (senão o "já aplicado" mentiria)
        e = w.event(old)
        same = lambda x: json.dumps(x, sort_keys=True, ensure_ascii=False)  # noqa: E731
        if same(e.get("effects", [])) != same(spec.get("effects", [])) or (e.get("cause") or None) != (spec.get("cause") or None):
            raise SimError(f"a operação '{gen_key.split(':')[-1]}' já foi usada com outros dados ({old}); use outro --op")
    eid = w.schedule(dict(spec, due=w.now), cause=spec.get("cause"), gen_key=gen_key, declared=declared)
    e = w.event(eid)
    if e["status"] == "processado":
        return e, True
    if e["status"] == "cancelado" or is_permanent(e):
        hint = (f"; {describe_application(e)} — encerre com 'time cancel {eid} --accept-partial --reason ...'"
                if is_permanent(e) and application(e)[0] == "parcial" else "")
        raise SimError(f"{eid} está '{e['status']}'" + (" (falha permanente)" if is_permanent(e) else "") + hint + " — use outro --op")
    st, info = process_event(w, e)
    if st != "processado":
        raise SimError(f"{eid}: {info} — nada mais será tentado; registre a decisão com 'time cancel {eid} --reason ...'")
    return w.event(eid), False


def op_sig(data):
    """Assinatura estável dos dados de uma operação: JSON canônico (chaves ordenadas, sem espaços) → sha256."""
    import hashlib
    raw = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def op_prev(events, gen_key, sig):
    """Evento já criado por esta operação (--op) ou None. Mesmo --op com outra assinatura = conflito, nada muda.
    A chave e a assinatura ficam na MESMA linha do evento (gravação única), então sobrevivem a timeout/queda."""
    prev = next((e for e in events if gen_key and e.get("gen_key") == gen_key), None)
    if prev and prev.get("op_sig") != sig:
        raise SimError(f"a operação '{gen_key.split(':', 1)[1]}' já foi usada com outros dados ({prev['id']}); use outro --op")
    return prev


def _walk_str(x):
    if isinstance(x, dict):
        for v in x.values():
            yield from _walk_str(v)
    elif isinstance(x, list):
        for v in x:
            yield from _walk_str(v)
    elif isinstance(x, str):
        yield x


PRECHECKS = {}


def precheck(name):
    """Validação ESTÁTICA de um tipo de efeito (antes de criar o evento). Levanta SimError com o motivo."""
    def deco(f):
        PRECHECKS[name] = f
        return f
    return deco


def effect_problems(w, effs, what="efeitos"):
    """Validação estática → (invalidos, pendentes).
    invalidos: a DEFINIÇÃO nunca poderá ser aplicada (formato, tipo, regra de valor/política — SimPermanent dos prechecks);
    pendentes: dependem do estado do mundo (id ainda inexistente, entidade ausente) — podem se resolver até a execução."""
    import world
    bad, pending = [], []
    for i, f in enumerate(effs if isinstance(effs, list) else [effs]):
        tag = f"{what}[{i}] ({f.get('type') if isinstance(f, dict) else f!r})"
        try:
            check_shape(f, "effect")
        except SimError as e:
            bad.append(f"{tag}: {e}")
            continue
        try:
            if f["type"] in PRECHECKS:
                PRECHECKS[f["type"]](w, f)
        except SimPermanent as e:
            bad.append(f"{tag}: {e}")
        except SimError as e:
            pending.append(f"{tag}: {e}")
        bad += [f"{tag}: {p}" for p in agency_problems(f, "efeito", w)]
        missing = sorted({v for v in _walk_str(f) if world.ID_ANY.fullmatch(v) and not (w.exists(v) or w.event(v))})
        pending += [f"{tag}: referência inexistente: {v}" for v in missing]
    return bad, pending


def check_effects(w, effs, what="efeitos"):
    """Validação ESTRITA (comandos que criam eventos a partir de entrada do usuário: life, econ, world feel…):
    definição inválida OU referência ainda inexistente = recusa. → lista de problemas (vazia = ok)."""
    bad, pending = effect_problems(w, effs, what)
    return bad + pending


def process_due(cdir):
    load_modules()
    w = World(cdir)
    log = []
    run_until(w, w.now, log)
    return w, log


# ---------------------------------------------------------------- conhecimento
def facts_path(cdir):
    return Path(cdir) / "state" / "sim" / "facts.json"


def load_facts(cdir):
    f = load_json(facts_path(cdir), {"facts": {}, "known": {}})
    f.setdefault("facts", {})
    f.setdefault("known", {})
    return f


def knowledge(cdir, who):
    return load_facts(cdir)["known"].get(who, {})


def knows(cdir, who, fact, minimum="suspeita"):
    k = knowledge(cdir, who).get(fact)
    return bool(k) and KNOW_RANK[k["status"]] >= KNOW_RANK[minimum]


def _learn(F, who, fact, status, source, at, confidence, event=None):
    if fact not in F["facts"]:
        raise SimError(f"fato '{fact}' não existe")
    if status not in KNOW_RANK:
        raise SimError(f"status de conhecimento inválido: {status}")
    if status == "sabe" and not F["facts"][fact].get("true", True):
        status = "acredita"  # ninguém 'sabe' algo falso; no máximo acredita
    cur = F["known"].setdefault(who, {}).get(fact)
    if cur and KNOW_RANK[cur["status"]] >= KNOW_RANK[status]:
        return cur
    F["known"][who][fact] = {"status": status, "source": source, "at": at, "confidence": round(confidence, 2)}
    evs = list(dict.fromkeys(event if isinstance(event, list) else [event] if event else []))
    if evs:  # evento(s) em que aprendeu (base da cadeia causal das decisões que usam este conhecimento)
        F["known"][who][fact].update({"event": evs[0]} if len(evs) == 1 else {"events": evs})
    return F["known"][who][fact]


def knowledge_sources(w, conds):
    """Eventos em que quem decide aprendeu o que as condições 'knows' VERDADEIRAS usam (all/any; nunca 'not'). É o evento
    que ENSINOU (testemunho, relato, 'knowledge.learn', ou o(s) evento(s) dado(s) em 'fact learn --source evt-…'), nunca a
    origem do conteúdo do fato. Conhecimento sem evento (fonte em texto livre, versões antigas) não gera ligação."""
    F, out = load_facts(w.dir), []

    def walk(c):
        if c.get("type") in ("all", "any"):
            for x in c.get("of", []):
                walk(x)
        elif c.get("type") == "knows" and check(w, c)[0]:
            k = F["known"].get(c.get("who"), {}).get(c.get("fact")) or {}
            for src in k.get("events") or [k.get("event") or k.get("source")]:
                if isinstance(src, str) and src.startswith("evt-") and w.event(src) and src not in out:
                    out.append(src)
    for c in conds or []:
        walk(c)
    return out


@effect("fact.create")
def _fx_fact(w, f, key, ev):
    F = load_facts(w.dir)
    if once(F, key):
        if f["fact"] in F["facts"]:
            raise SimError(f"fato '{f['fact']}' já existe")
        F["facts"][f["fact"]] = {"text": f["text"], "true": f.get("true", True), "secret": f.get("secret", True),
                                 "about": f.get("about", []), "origin": ev["id"]}
        save_json(facts_path(w.dir), F)
    return {"fato": f["fact"]}


@effect("knowledge.learn")
def _fx_learn(w, f, key, ev):
    if not w.exists(f["who"]):
        raise SimError(f"entidade '{f['who']}' não existe")
    F = load_facts(w.dir)
    if once(F, key):
        _learn(F, f["who"], f["fact"], f.get("status", "sabe"), f.get("source", ev["id"]), w.now, f.get("confidence", 1.0), ev["id"])
        save_json(facts_path(w.dir), F)
    return {"aprendeu": f["who"], "fato": f["fact"]}


@effect("knowledge.witness")
def _fx_witness(w, f, key, ev):
    """Quem está no local (frontmatter location) no momento passa a saber. Ninguém mais."""
    F = load_facts(w.dir)
    present = sorted(k for k, e in w.ctx.ent.items() if e["meta"].get("location") == f["loc"]
                     and k.startswith(("npc-", "pc-")) and not e["meta"].get("status", "").startswith("mort"))
    present = [p for p in present if p not in f.get("except", [])]
    if "npc.free" in CONDITIONS:  # quem está em viagem não testemunha no local de origem
        present = [p for p in present if not p.startswith("npc-") or check(w, {"type": "npc.free", "npc": p})[0]]
    if once(F, key):
        for p in present:
            _learn(F, p, f["fact"], "sabe", ev["id"], w.now, 1.0, ev["id"])
        save_json(facts_path(w.dir), F)
    return {"testemunhas": present}


@effect("knowledge.tell")
def _fx_tell(w, f, key, ev):
    """Transmissão plausível: quem conta precisa saber/acreditar; o ouvinte acredita (ou suspeita)."""
    F = load_facts(w.dir)
    src = F["known"].get(f["from"], {}).get(f["fact"])
    if not src or KNOW_RANK[src["status"]] < KNOW_RANK["acredita"]:
        raise SimError(f"{f['from']} não conhece '{f['fact']}' — não pode contar")
    if not w.exists(f["to"]):
        raise SimError(f"entidade '{f['to']}' não existe")
    if once(F, key):
        conf = src["confidence"] * f.get("trust", 0.8)
        _learn(F, f["to"], f["fact"], "acredita" if conf >= 0.5 else "suspeita", f["from"], w.now, conf, ev["id"])
        save_json(facts_path(w.dir), F)
    return {"contou": f["from"], "para": f["to"], "fato": f["fact"]}


@condition("knows")
def _c_knows(w, c):
    ok = knows(w.dir, c["who"], c["fact"], c.get("min", "acredita"))
    return ok, f"{c['who']} {'conhece' if ok else 'NÃO conhece'} {c['fact']}"


# ---------------------------------------------------------------- recursos
def res_path(cdir):
    return Path(cdir) / "state" / "sim" / "resources.json"


def load_res(cdir):
    r = load_json(res_path(cdir), {"holdings": {}, "allow_negative": []})
    r.setdefault("holdings", {})
    r.setdefault("allow_negative", [])
    return r


def balance(cdir, who, res):
    return load_res(cdir)["holdings"].get(who, {}).get(res, 0)


def _qty(q):
    if not isinstance(q, (int, float)) or isinstance(q, bool) or q <= 0:
        raise SimPermanent(f"quantidade inválida: {q!r} (precisa ser > 0)")
    return q


@precheck("resource.transfer")
def _pc_transfer(w, f):
    _qty(f.get("qty"))
    if f.get("from") == f.get("to"):
        raise SimPermanent("transferência para si mesmo")


@precheck("resource.produce")
def _pc_produce(w, f):
    _qty(f.get("qty"))
    if not f.get("source"):
        raise SimPermanent("resource.produce exige 'source' (origem do recurso)")


@precheck("resource.consume")
def _pc_consume(w, f):
    _qty(f.get("qty"))


def _take(R, who, res, q):
    have = R["holdings"].get(who, {}).get(res, 0)
    if have - q < 0 and who not in R["allow_negative"]:
        raise SimError(f"{who} tem {have} {res}; não pode gastar {q} (saldo negativo não permitido)")
    R["holdings"].setdefault(who, {})[res] = have - q


@effect("resource.transfer")
def _fx_transfer(w, f, key, ev):
    q = _qty(f["qty"])
    for x in (f["from"], f["to"]):
        if not w.exists(x):
            raise SimError(f"entidade '{x}' não existe")
    if f["from"] == f["to"]:
        raise SimPermanent("transferência para si mesmo")
    R = load_res(w.dir)
    if once(R, key):
        _take(R, f["from"], f["res"], q)
        h = R["holdings"].setdefault(f["to"], {})
        h[f["res"]] = h.get(f["res"], 0) + q
        save_json(res_path(w.dir), R)
    return {"transferiu": q, "res": f["res"], "de": f["from"], "para": f["to"]}


@effect("resource.produce")
def _fx_produce(w, f, key, ev):
    """Única forma de criar recurso: exige 'source' (de onde veio) para auditoria."""
    q = _qty(f["qty"])
    if not f.get("source"):
        raise SimPermanent("resource.produce exige 'source' (origem do recurso)")
    if not w.exists(f["who"]):
        raise SimError(f"entidade '{f['who']}' não existe")
    R = load_res(w.dir)
    if once(R, key):
        h = R["holdings"].setdefault(f["who"], {})
        h[f["res"]] = h.get(f["res"], 0) + q
        save_json(res_path(w.dir), R)
    return {"produziu": q, "res": f["res"], "quem": f["who"], "origem": f["source"]}


@effect("resource.consume")
def _fx_consume(w, f, key, ev):
    q = _qty(f["qty"])
    R = load_res(w.dir)
    if once(R, key):
        _take(R, f["who"], f["res"], q)
        save_json(res_path(w.dir), R)
    return {"consumiu": q, "res": f["res"], "quem": f["who"]}


@condition("resource.gte")
def _c_res(w, c):
    have = balance(w.dir, c["who"], c["res"])
    return have >= c["qty"], f"{c['who']} tem {have} {c['res']} (precisa {c['qty']})"


# ---------------------------------------------------------------- genéricos
def flags_path(cdir):
    return Path(cdir) / "state" / "sim" / "flags.json"


@effect("log")
def _fx_log(w, f, key, ev):
    return {"nota": f.get("text", "")}


@effect("flag.set")
def _fx_flag(w, f, key, ev):
    F = load_json(flags_path(w.dir), {})
    F[f["name"]] = f["value"]
    save_json(flags_path(w.dir), F)
    return {"flag": f["name"], "valor": f["value"]}


@condition("flag")
def _c_flag(w, c):
    v = load_json(flags_path(w.dir), {}).get(c["name"])
    return v == c.get("value", True), f"flag {c['name']}={v!r}"


ENTITY_FIELDS = {"location", "status", "faction", "giver", "region", "parent", "home"}


@precheck("entity.set")
def _pc_entity(w, f):
    if f.get("field") not in ENTITY_FIELDS:
        raise SimPermanent(f"campo '{f.get('field')}' não é alterável pela simulação ({', '.join(sorted(ENTITY_FIELDS))})")


@effect("entity.set")
def _fx_entity(w, f, key, ev):
    """Muda um campo do frontmatter (fonte de verdade de local/status). Idempotente por natureza."""
    _pc_entity(w, f)
    p = w.entity_path(f["id"])
    if not p:
        raise SimError(f"entidade '{f['id']}' não tem arquivo")
    v = str(f["value"])
    if f["field"] in ("location", "faction", "region", "parent", "home") and not w.exists(v):
        raise SimError(f"'{v}' não existe")
    before = read_meta(p).get(f["field"])
    if before != v:
        set_meta(p, f["field"], v)
    return {"entidade": f["id"], "campo": f["field"], "de": before, "para": v}


@condition("entity")
def _c_entity(w, c):
    e = w.ctx.ent.get(c["id"])
    v = e["meta"].get(c["field"]) if e else None
    ok = v in c["in"] if "in" in c else v == c.get("eq")
    return ok, f"{c['id']}.{c['field']}={v!r}"


@condition("event")
def _c_event(w, c):
    e = w.event(c["id"])
    st = e["status"] if e else None
    return st == c.get("status", "processado"), f"{c['id']} está {st}"


@condition("time")
def _c_time(w, c):
    ok = (w.now >= c["after"] if "after" in c else True) and (w.now < c["before"] if "before" in c else True)
    return ok, f"agora={w.fmt(w.now)}"


@condition("map.revealed")
def _c_map(w, c):
    import maps
    try:
        m = maps.load_map(w.dir, c["map"])
    except maps.MapError:
        return False, f"mapa {c['map']} não existe"
    st = m["discovery"].get(c["id"])
    return bool(st), f"{c['id']} no mapa {c['map']}: {st or 'não revelado'}"


@effect("schedule")
def _fx_schedule(w, f, key, ev):
    """Agenda consequência futura, com causa = este evento. Idempotente pela chave."""
    spec = dict(f["event"])
    spec["due"] = w.now + parse_duration(f["in"], w.cal) if "in" in f else w.now
    eid = w.schedule(spec, cause=ev["id"], gen_key=key)
    return {"agendou": eid, "para": w.fmt(spec["due"])}


@effect("relation.add")
def _fx_rel(w, f, key, ev):
    import world
    recs = read_jsonl(w.dir / "state" / "relations.jsonl")
    if any(r.get("key") == key for r in recs):
        return {"relacao": "já registrada"}
    for x in (f["a"], f["b"]):
        if not w.exists(x):
            raise SimError(f"'{x}' não existe")
    rid = world._next_id(recs, "rel-")
    append_jsonl(w.dir / "state" / "relations.jsonl", {"op": "add", "id": rid, "a": f["a"], "b": f["b"], "type": f["rel"],
                 "note": f.get("note", ""), "secret": f.get("secret", False), "session": w.session(),
                 "t": real_now().isoformat(), "key": key, "cause": ev["id"]})
    return {"relacao": rid}


@effect("relation.end")
def _fx_rel_end(w, f, key, ev):
    recs = read_jsonl(w.dir / "state" / "relations.jsonl")
    if any(r.get("key") == key for r in recs):
        return {"encerrou": f["id"]}
    append_jsonl(w.dir / "state" / "relations.jsonl", {"op": "end", "id": f["id"], "note": f.get("note", ""),
                 "session": w.session(), "t": real_now().isoformat(), "key": key, "cause": ev["id"]})
    return {"encerrou": f["id"]}


# Sentimentos DIRECIONAIS (quem sente → por quem), registrados como variações {"op":"feel"} em relations.jsonl.
# O valor atual é a soma do histórico, limitada à faixa a cada passo — nada é gravado duas vezes.
# Dimensão nova = uma linha aqui. Registros antigos com dimensão desconhecida são preservados e somados sem faixa.
FEEL_DIMS = {"afeto": (-100, 100), "confianca": (-100, 100), "respeito": (-100, 100), "admiracao": (-100, 100),
             "intimidade": (0, 100), "tensao": (0, 100), "atracao": (0, 100)}


def feelings(records):
    """{(de, para): {dimensão: valor}} a partir dos registros de relations.jsonl, em ordem de gravação."""
    out = {}
    for r in records:
        if r.get("op") != "feel":
            continue
        cur = out.setdefault((r["from"], r["to"]), {})
        v = cur.get(r["dim"], 0) + r["delta"]
        lo, hi = FEEL_DIMS.get(r["dim"], (None, None))
        cur[r["dim"]] = v if lo is None else max(lo, min(hi, v))
    return out


@precheck("relation.feel")
def check_feel(w, f):
    if f.get("dim") not in FEEL_DIMS:
        raise SimPermanent(f"dimensão '{f.get('dim')}' desconhecida ({', '.join(FEEL_DIMS)})")
    if not isinstance(f.get("delta"), int) or isinstance(f["delta"], bool) or not f["delta"]:
        raise SimPermanent("delta precisa ser inteiro diferente de zero")
    if f.get("from") == f.get("to"):
        raise SimPermanent("sentimento de uma entidade por ela mesma")
    if str(f.get("from", "")).startswith("pc-") and not f.get("declared"):
        raise SimPermanent(f"sentimento de {f['from']} só com declaração/autorização do jogador (declared: true)")
    for x in (f.get("from"), f.get("to")):
        if not x or not w.exists(x):
            raise SimError(f"'{x}' não existe")


@effect("relation.feel")
def _fx_feel(w, f, key, ev):
    """O que 'from' sente por 'to' muda em 'delta'. Nunca recíproco. Sentimento de PJ exige declared: true
    (o jogador declarou ou autorizou) — o motor jamais decide o que o protagonista sente."""
    path = w.dir / "state" / "relations.jsonl"
    recs = read_jsonl(path)
    old = next((r for r in recs if r.get("key") == key), None)
    if not old:
        check_feel(w, f)
        old = {"op": "feel", "from": f["from"], "to": f["to"], "dim": f["dim"], "delta": f["delta"],
               "reason": f.get("reason", ""), "declared": bool(f.get("declared")) and declared_effect(ev, f),  # nunca só pelo dado
               "session": w.session(),
               "at": w.now, "t": real_now().isoformat(), "key": key, "cause": ev["id"]}
        append_jsonl(path, old)
        recs.append(old)
    val = feelings(recs).get((old["from"], old["to"]), {}).get(old["dim"])
    return {"de": old["from"], "para": old["to"], "dim": old["dim"], "delta": old["delta"], "valor": val}


@condition("feel")
def _c_feel(w, c):
    v = feelings(read_jsonl(w.dir / "state" / "relations.jsonl")).get((c["from"], c["to"]), {}).get(c["dim"], 0)
    ok = (v >= c["gte"] if "gte" in c else True) and (v <= c["lte"] if "lte" in c else True)
    return ok, f"{c['from']}→{c['to']} {c['dim']}={v}"


@effect("map.reveal")
def _fx_map_reveal(w, f, key, ev):
    """Revelação EXPLÍCITA (agendada pelo mestre) usando o mecanismo dos mapas."""
    import maps
    m = maps.load_map(w.dir, f["map"])
    if m["discovery"].get(f["id"]) != f.get("status", "conhecido"):
        m["discovery"][f["id"]] = f.get("status", "conhecido")
        maps.save_map(w.dir, m, f"revelado por {ev['id']}")
        maps.render_campaign(w.dir)
    return {"revelou": f["id"], "mapa": f["map"]}


# ---------------------------------------------------------------- verificação
@verifier
def _v_core(w):
    P = []
    evs = w.events()
    ids = {e["id"] for e in evs}
    for e in evs:
        for k in ("cause", "prev", "corrects"):
            if e.get(k) and e[k] not in ids:
                P.append(f"[evento] {e['id']}: {k} '{e[k]}' não existe")
        P += [f"[evento] {e['id']}: origem '{s}' não existe" for s in e.get("sources") or [] if s not in ids]
        app = application(e)[0] if e["status"] in ("falhou", "processando") else None
        part = f"; aplicação PARCIAL: {describe_application(e)}" if app == "parcial" else ""
        if is_permanent(e):
            P.append(f"[evento] {e['id']} teve falha permanente (não bloqueia o relógio; não será retentado): "
                     f"{(e.get('result') or {}).get('erro')}{part} — registre a decisão com 'time cancel {e['id']} --reason ...'"
                     + (" --accept-partial" if app == "parcial" else ""))
        elif e["status"] in ("processando", "falhou"):
            P.append(f"[evento] {e['id']} ficou '{e['status']}'{part} — {RECOVER_HINT}")
        if w.clock and e["status"] == "pendente" and e.get("due", 0) < w.now:
            P.append(f"[evento] {e['id']} venceu em {w.fmt(e['due'])} e não foi processado — 'time process'")
    if w.clock and w.clock.get("pending_op"):
        P.append(f"[relógio] avanço interrompido '{w.clock['pending_op']['op']}' — 'time resume'")
    R = load_res(w.dir)
    for who, h in R["holdings"].items():
        for res, q in h.items():
            if q < 0 and who not in R["allow_negative"]:
                P.append(f"[recursos] {who} com saldo negativo de {res}: {q}")
    F = load_facts(w.dir)
    for who, ks in F["known"].items():
        for fact in ks:
            if fact not in F["facts"]:
                P.append(f"[conhecimento] {who} conhece fato inexistente '{fact}'")
    return P


def verify(cdir):
    load_modules()
    try:
        w = World(cdir, require_clock=False)
    except SimError as e:
        return [f"[simulação] {e}"]
    out = []
    for v in VERIFIERS:
        try:
            out += v(w)
        except SimError as e:
            out.append(f"[simulação] {e}")
    return out


# ---------------------------------------------------------------- CLI
def _w(a, require_clock=True):
    load_modules()
    return World(camp_dir(a.campaign), require_clock)


def _json_arg(s, what):
    if not s:
        return []
    try:
        v = json.loads(Path(s[1:]).read_text(encoding="utf-8") if s.startswith("@") else s)
    except (json.JSONDecodeError, OSError) as e:
        raise SimError(f"{what}: JSON inválido ({e})")
    return v if isinstance(v, list) else [v]


def _print_log(w, log):
    for e, st, info in log:
        tag = " [MESTRE]" if e.get("hidden") else ""
        extra = f" — {info}" if st in ("cancelado", "adiado", "falhou") and info else ""
        print(f"  {w.fmt(e['due'])} {e['id']} [{e.get('type')}] {st}{tag}: {e.get('text', '')}{extra}")


def cmd_init(a):
    d = camp_dir(a.campaign)
    if clock_path(d).exists():
        raise SimError("relógio já iniciado — não sobrescrevo")
    cal = validate_calendar(json.loads(Path(a.calendar).read_text(encoding="utf-8"))) if a.calendar else DEFAULT_CAL
    start = parse_when(a.start, cal, 0) if a.start else 8 * cal["minutes_per_hour"]
    if a.today:
        cal = dict(cal, week_offset=_week_offset(cal, a.today, start))
    seed = a.seed if a.seed is not None else random.SystemRandom().randint(1, 2 ** 31 - 1)
    save_json(clock_path(d), {"calendar": cal, "now": start, "seed": seed, "ops": []})
    print(f"Relógio iniciado: {fmt(start, cal)} (seed {seed})")


def _week_offset(cal, today, t):
    """Deslocamento para que o dia de t seja 'today'."""
    check_days([today], cal)
    return (cal["week"].index(today) - t // _units(cal)[1]) % len(cal["week"])


def cmd_calendar(a):
    """Só ACRESCENTA ao calendário (semana, folgas, feriados): meses, horas e uma semana já definida não mudam,
    para nenhuma data passada ser reinterpretada. Cada mudança vira um evento 'calendario'."""
    w = _w(a)
    old = w.clock["calendar"]
    cal = json.loads(json.dumps(old))
    if a.week:
        if old.get("week"):
            raise SimError("o calendário já tem semana; trocá-la mudaria os dias da semana do passado — não altero")
        if not a.today:
            raise SimError("--week exige --today <nome do dia de hoje>")
        cal["week"] = [x.strip() for x in a.week.split(",") if x.strip()]
        validate_calendar(cal)
        cal["week_offset"] = _week_offset(cal, a.today, w.now)
    elif a.today:
        raise SimError("--today só vale junto com --week")
    if a.rest is not None:
        new = [x.strip() for x in a.rest.split(",") if x.strip()]
        if new != cal.get("rest_days", []):  # a regra antiga continua valendo para o passado
            day0 = w.now - w.now % _units(cal)[1]  # a partir do início de hoje
            cal["rest_history"] = cal.get("rest_history", []) + [{"until": day0, "days": cal.get("rest_days", [])}]
            cal["rest_days"] = new
    for h in a.holiday or []:
        date, _, name = h.partition("=")
        if not any(x["date"] == date.strip() for x in cal.get("holidays", [])):
            cal.setdefault("holidays", []).append({"date": date.strip(), "name": name.strip(),
                                                   "since": w.now - w.now % _units(cal)[1]})
    validate_calendar(cal)
    if cal == old:
        print("Calendário sem mudanças.")
        return
    diff = {k: cal.get(k) for k in cal if cal.get(k) != old.get(k)}
    # evento primeiro (idempotente pelo conteúdo novo): se cair antes de gravar o relógio, repetir não duplica
    w.record(f"Calendário ampliado: {', '.join(sorted(diff))}", "calendario",
             gen_key=f"calendar:{json.dumps(cal, sort_keys=True, ensure_ascii=False)}", result=diff)
    c = load_clock(w.dir)
    c["calendar"] = cal
    save_json(clock_path(w.dir), c)
    print(f"Calendário atualizado ({', '.join(sorted(diff))}). Hoje: {fmt(w.now, cal)}")


def cmd_now(a):
    w = _w(a)
    if a.public:  # jogadores: só a data, nada do que está agendado
        print(w.fmt(w.now))
        return
    print(f"{w.fmt(w.now)} (t={w.now})")
    up = sorted((e for e in w.events() if e["status"] == "pendente"), key=_order)[:3]
    for e in up:
        print(f"  próximo: {w.fmt(e['due'])} {e['id']} {e.get('text', '')}" + (" [MESTRE]" if e.get("hidden") else ""))


def cmd_advance(a):
    d = camp_dir(a.campaign)
    w0 = World(d)
    mins = parse_duration(a.duration, w0.cal)
    w, log = advance(d, mins, a.op, a.reason)
    print(f"Tempo avançou {a.duration} ({a.reason}): agora {w.fmt(w.now)}. {len(log)} evento(s) processado(s):")
    _print_log(w, log)
    _warn_permanent(log)


def _warn_permanent(log):
    bad = [e["id"] for e, st, _ in log if st == "falhou"]
    if bad:
        print(f"ATENÇÃO: {len(bad)} evento(s) com falha permanente ({', '.join(bad)}): não bloqueiam o relógio e não "
              f"serão retentados. Revise com 'world verify' e registre a decisão com 'time cancel <evt> --reason ...'.")


def cmd_resume(a):
    w, log = resume(camp_dir(a.campaign))
    print(f"Avanço retomado e concluído: agora {w.fmt(w.now)}")
    _print_log(w, log)
    _warn_permanent(log)


def cmd_process(a):
    w, log = process_due(camp_dir(a.campaign))
    print(f"{len(log)} evento(s) vencido(s) processado(s) em {w.fmt(w.now)}")
    _print_log(w, log)
    _warn_permanent(log)


def cmd_schedule(a):
    w = _w(a)
    due = parse_when(a.at, w.cal, w.now) if a.at else (w.now + parse_duration(a.in_, w.cal) if a.in_ else w.now)
    ents = [x.strip() for x in (a.entities or "").split(",") if x.strip()]
    effs, conds = _json_arg(a.effects, "--effects"), _json_arg(a.conditions, "--conditions")
    sig = gk = None
    if a.op is not None:  # --op: repetir a MESMA operação (inclusive após timeout/avanço) não agenda outro evento
        if not a.op.strip():
            raise SimError("--op vazio")
        # quando: relativo (--in, --at +D, nenhum = agora) guarda a DURAÇÃO; absoluto guarda o instante
        rel = a.at.strip()[1:] if a.at and a.at.strip().startswith("+") else (None if a.at else a.in_ or "0min")
        when = {"rel": parse_duration(rel, w.cal)} if rel is not None else {"abs": due}
        gk, sig = f"schedule:{a.op}", op_sig({"text": a.text, "when": when, "type": a.type, "priority": a.priority,
                                              "entities": ents, "loc": a.loc, "cause": a.cause, "effects": effs,
                                              "conditions": conds, "on_unmet": a.on_unmet, "hidden": bool(a.hidden)})
        prev = op_prev(w.events(), gk, sig)
        if prev:
            print(f"Operação '{a.op}' já agendou {prev['id']} para {prev.get('date')} (status: {prev['status']}) — nada novo agendado")
            return
    # Contrato: campos do evento (--entities, --loc, --cause) precisam existir AGORA. Definição comprovadamente
    # inválida (formato, tipo, faixa, campo obrigatório, política) é RECUSADA antes de gravar. Dentro de
    # --effects/--conditions, ids podem ser resolvidos até a execução (ex.: NPC criado depois): só AVISO; se ainda
    # faltarem, falha RECUPERÁVEL. O que só o estado do momento decide (saldo, transição de missão, rota removida
    # depois, presença já registrada) continua sendo julgado na execução: recuperável ou permanente.
    for x in ents + [y for y in (a.loc, a.cause) if y]:
        if not (w.exists(x) or w.event(x)):
            raise SimError(f"referência inexistente: {x}")
    import world
    for c in conds:
        check_shape(c, "condition")
    bad, _ = effect_problems(w, effs, "--effects")
    if bad:  # comprovadamente inválido: recusado ANTES de gravar qualquer coisa
        raise SimError("definição inválida — nada foi agendado:\n  " + "\n  ".join(bad))
    missing = sorted({v for v in _walk_str([effs, conds]) if world.ID_ANY.fullmatch(v) and not (w.exists(v) or w.event(v))})
    eid = w.schedule({"type": a.type, "text": a.text, "due": due, "priority": a.priority, "entities": ents, "loc": a.loc,
                      "conditions": conds, "effects": effs, "on_unmet": a.on_unmet, "hidden": a.hidden, "op_sig": sig},
                     cause=a.cause, gen_key=gk)
    print(f"Agendado {eid} para {w.fmt(due)}" + (" [MESTRE]" if a.hidden else ""))
    if missing:
        print(f"AVISO: ainda não existem: {', '.join(missing)} — precisam existir até {w.fmt(due)}; senão o evento falha "
              f"(recuperável: crie e retome, ou cancele)")



def cmd_cancel(a):
    """Cancelar NUNCA desfaz fatos. Não iniciado → cancela. Parcial → exige escolha explícita: concluir ('time retry',
    mesmo --op, 'time resume') ou encerrar mantendo o que já foi aplicado (--accept-partial, auditado)."""
    w = _w(a)
    e = w.event(a.id) or die(f"evento '{a.id}' não existe")
    st, n, incerto = application(e)
    if st == "concluido":
        raise SimError(f"{a.id} já foi concluído — eventos concluídos não são cancelados nem apagados; "
                       f"registre uma correção ('world event-add ... --corrects {a.id} --reason ...')")
    if st == "cancelado":
        raise SimError(f"{a.id} já está cancelado ({(e.get('result') or {}).get('motivo', '')}) — nada a fazer")
    effs = [f.get("type") for f in e.get("effects", [])]
    if st == "parcial":
        if not a.accept_partial:
            raise SimError(f"{a.id} foi aplicado PARCIALMENTE: {describe_application(e)}. Cancelar NÃO desfaz o que já "
                           f"aconteceu. Concluir: 'time retry {a.id}' (ou repita o comando com o mesmo --op / 'time resume'). "
                           f"Encerrar mantendo o que já foi aplicado: 'time cancel {a.id} --accept-partial --reason ...'")
        rec = {"motivo": a.reason, "parcial": True, "aplicados": effs[:n], "nao_aplicados": effs[n:],
               "incerto": effs[n] if incerto and n < len(effs) else None}
        if rec["incerto"]:  # o que se sabe do efeito incerto fica no próprio registro do encerramento
            r = e.get("result") or {}
            rec["subefeitos_concluidos"] = list(subeffects(e, n))
            if isinstance(r, dict) and r.get("falhou_em"):
                rec["falhou_em"] = r["falhou_em"]
        w.set_status(a.id, "cancelado", rec)
        print(f"{a.id} encerrado com aplicação PARCIAL ({a.reason}): mantidos {effs[:n] or 'nenhum'}; não aplicados {effs[n:]}"
              + (f"; '{effs[n]}' pode ter sido aplicado em parte — confira com 'time inspect {a.id}'" if rec["incerto"] else ""))
    else:
        w.set_status(a.id, "cancelado", {"motivo": a.reason, "parcial": False})
        print(f"{a.id} cancelado ({a.reason}); nada dele havia sido aplicado")
    if load_clock(w.dir).get("pending_op"):
        print("Há um avanço interrompido: conclua com 'time resume'.")


def cmd_retry(a):
    """Reprocessa AGORA um evento falho/interrompido (recuperável), a partir do primeiro efeito não aplicado."""
    w = _w(a)
    e = w.event(a.id) or die(f"evento '{a.id}' não existe")
    if e["status"] not in ("falhou", "processando"):
        raise SimError(f"{a.id} está '{e['status']}' — só eventos falhos ou interrompidos podem ser retomados")
    if is_permanent(e):
        raise SimError(f"{a.id} teve falha permanente: não será retentado ({describe_application(e)}); "
                       f"encerre com 'time cancel {a.id} --reason ...'" + (" --accept-partial" if application(e)[0] == "parcial" else ""))
    before = describe_application(e)
    st, info = process_event(w, e)
    if st != "processado":
        raise SimError(f"{a.id}: {info}")
    print(f"{a.id} concluído (antes: {before}).")
    if load_clock(w.dir).get("pending_op"):
        print("Há um avanço interrompido: conclua com 'time resume'.")


def cmd_inspect(a):
    """Auditoria de um evento: o que foi aplicado, o que falta, o que é incerto, e todo o histórico de status."""
    w = _w(a, False)
    e = w.event(a.id) or die(f"evento '{a.id}' não existe")
    st, n, incerto = application(e)
    print(f"{e['id']} [{e.get('type')}] {e.get('text', '')}" + (" [MESTRE]" if e.get("hidden") else ""))
    print(f"status: {e['status']}" + (" (falha permanente)" if is_permanent(e) else "") + f" | aplicação: {st}"
          + (f" | causa: {e['cause']}" if e.get("cause") else "") + (f" | data: {e.get('date')}" if e.get("date") else "")
          + (" | declarado pelo jogador" if is_declared(e) else ""))
    if e.get("sources"):
        print(f"origem do conhecimento: {', '.join(e['sources'])}")
    last = e.get("result") if isinstance(e.get("result"), dict) else {}
    done = {h["result"]["aplicados"] - 1: h["result"].get("resultado") for h in e.get("history", [])
            if isinstance(h.get("result"), dict) and "resultado" in h["result"]}
    for i, r in enumerate(((e.get("result") or {}).get("efeitos") or []) if st == "concluido" else []):
        done[i] = r
    for i, f in enumerate(e.get("effects", [])):
        mark = "aplicado" if (i < n or st == "concluido") else ("INCERTO" if incerto and i == n else "não aplicado")
        auth = (done.get(i) or {}).get("autorizacao") if isinstance(done.get(i), dict) else None
        print(f"  #{i} {f.get('type')}: {mark}" + (" (composto)" if is_composite(f) else "") + (f" — autorização: {auth}" if auth else ""))
        for k, r in subeffects(e, i).items():
            sa = r["sub_resultado"].get("autorizacao") if isinstance(r.get("sub_resultado"), dict) else None
            print(f"     sub {k} {r.get('tipo')}: concluído" + (f" — autorização: {sa}" if sa else ""))
        if last.get("falhou_em", "").startswith(f"{e['id']}#{i}/"):
            print(f"     sub {last['falhou_em']}: FALHOU — {last.get('erro')}")
    for h in e.get("history", []):
        print(f"  histórico: {h.get('status')} em t={h.get('at')} {h.get('result') or ''}")


def cmd_upcoming(a):
    w = _w(a)
    evs = sorted((e for e in w.events() if e["status"] in ("pendente", "processando", "falhou")), key=_order)
    if not evs:
        print("Nenhum evento pendente.")
    for e in evs[:a.limit]:
        cond = " (com condições)" if e.get("conditions") else ""
        st = e["status"] + (" (permanente; não bloqueia)" if is_permanent(e) else "")
        print(f"{w.fmt(e['due'])} {e['id']} [{e.get('type')}] {st}{cond}{' [MESTRE]' if e.get('hidden') else ''}: {e.get('text', '')}"
              + (f" | causa: {e['cause']}" if e.get("cause") else ""))


def cmd_fact_add(a):
    w = _w(a, False)
    F = load_facts(w.dir)
    if a.id in F["facts"]:
        raise SimError(f"fato '{a.id}' já existe")
    about = [x.strip() for x in (a.about or "").split(",") if x.strip()]
    for x in about:
        if not w.exists(x):
            raise SimError(f"'{x}' não existe")
    F["facts"][a.id] = {"text": a.text, "true": not a.false, "secret": not a.public, "about": about, "origin": "mestre"}
    save_json(facts_path(w.dir), F)
    print(f"Fato {a.id} registrado ({'verdadeiro' if not a.false else 'FALSO (boato)'}, {'público' if a.public else 'secreto'})")


def cmd_learn(a):
    """--source em texto livre = de onde veio (sem ligação causal). --source evt-N[,evt-M] = evento(s) JÁ OCORRIDO(S) em que
    aprendeu: validado antes de gravar e usado como origem nas decisões (world chain/why). Nenhum evento é criado."""
    w = _w(a, False)
    if not w.exists(a.who):
        raise SimError(f"'{a.who}' não existe")
    refs = [x.strip() for x in a.source.split(",")]
    evs = refs if all(re.fullmatch(r"evt-\d+", x) for x in refs) else []
    for x in evs:
        e = w.event(x)
        if not e or e["status"] != "processado":
            raise SimError(f"--source {x}: evento {'inexistente' if not e else 'que não ocorreu (' + e['status'] + ')'} — nada foi gravado")
    F = load_facts(w.dir)
    k = _learn(F, a.who, a.fact, a.status, a.source, w.now, a.confidence, evs)
    save_json(facts_path(w.dir), F)
    print(f"{a.who} agora {k['status']} {a.fact} (fonte: {a.source})")


def cmd_knows(a):
    w = _w(a, False)
    F = load_facts(w.dir)
    ks = F["known"].get(a.who, {})
    if not ks:
        print(f"{a.who} não conhece nenhum fato registrado.")
    for fid, k in sorted(ks.items()):
        f = F["facts"].get(fid, {})
        verdade = "" if f.get("true", True) else " (FALSO)"
        print(f"{fid}: {k['status']} (confiança {k.get('confidence', '?')}, fonte {k.get('source', '?')}){verdade} — {f.get('text', '?')}")
    unknown = sorted(set(F["facts"]) - set(ks))
    if unknown and a.all:
        print("Desconhece: " + ", ".join(unknown))


def cmd_res(a):
    w = _w(a, False)
    R = load_res(w.dir)
    rows = {a.who: R["holdings"].get(a.who, {})} if a.who else R["holdings"]
    if not any(rows.values()):
        print("Sem recursos registrados.")
    for who, h in sorted(rows.items()):
        print(f"{who}: " + ", ".join(f"{k}={v:g}" for k, v in sorted(h.items())))


def _wrap(f):
    def g(a):
        try:
            f(a)
        except SimError as e:
            die(str(e))
    return g


def register(sp):
    t = sp.add_parser("time", help="relógio do mundo e eventos agendados").add_subparsers(dest="tcmd", required=True)
    c = t.add_parser("init", help="inicia o relógio (calendário configurável)")
    c.add_argument("--start", help="A/M/D HH:MM"); c.add_argument("--calendar", help="JSON do calendário")
    c.add_argument("--seed", type=int); c.add_argument("--today", help="dia da semana do início (calendário com semana)")
    c.add_argument("--campaign"); c.set_defaults(f=_wrap(cmd_init))
    c = t.add_parser("calendar", help="acrescenta semana, folgas e feriados ao calendário (nunca reinterpreta o passado)")
    c.add_argument("--week", help="dias separados por vírgula (só se ainda não houver semana)")
    c.add_argument("--today", help="nome do dia de hoje (com --week)"); c.add_argument("--rest", help="dias de folga, por vírgula")
    c.add_argument("--holiday", action="append", help="'M/D=Nome' (anual) ou 'A/M/D=Nome'"); c.add_argument("--campaign")
    c.set_defaults(f=_wrap(cmd_calendar))
    c = t.add_parser("now", help="data/hora do mundo e próximos eventos")
    c.add_argument("--public", action="store_true", help="só a data (para jogadores)"); c.add_argument("--campaign")
    c.set_defaults(f=_wrap(cmd_now))
    c = t.add_parser("advance", help="avança o tempo e processa o que vencer (exige --op única)")
    c.add_argument("duration", help="6h, 3d, 1d6h, 45min"); c.add_argument("--op", required=True, help="id único da ação (evita avanço duplo)")
    c.add_argument("--reason", required=True); c.add_argument("--campaign"); c.set_defaults(f=_wrap(cmd_advance))
    c = t.add_parser("resume", help="conclui um avanço interrompido"); c.add_argument("--campaign"); c.set_defaults(f=_wrap(cmd_resume))
    c = t.add_parser("process", help="processa eventos já vencidos, sem avançar"); c.add_argument("--campaign"); c.set_defaults(f=_wrap(cmd_process))
    c = t.add_parser("schedule", help="agenda evento futuro")
    c.add_argument("text"); c.add_argument("--in", dest="in_"); c.add_argument("--at"); c.add_argument("--type", default="evento")
    c.add_argument("--priority", type=int, default=50); c.add_argument("--entities"); c.add_argument("--loc"); c.add_argument("--cause")
    c.add_argument("--effects", help="JSON ou @arquivo.json"); c.add_argument("--conditions", help="JSON ou @arquivo.json")
    c.add_argument("--on-unmet", choices=["cancelar", "adiar"], default="cancelar"); c.add_argument("--hidden", action="store_true")
    c.add_argument("--op", help="id da operação: repetir com os mesmos dados não agenda outro evento")
    c.add_argument("--campaign"); c.set_defaults(f=_wrap(cmd_schedule))
    c = t.add_parser("cancel", help="cancela evento pendente/falho (registrado, não apagado; nunca desfaz fatos)")
    c.add_argument("id"); c.add_argument("--reason", required=True)
    c.add_argument("--accept-partial", action="store_true", help="encerra evento PARCIALMENTE aplicado mantendo o que já foi aplicado")
    c.add_argument("--campaign"); c.set_defaults(f=_wrap(cmd_cancel))
    c = t.add_parser("retry", help="retoma agora um evento falho/interrompido a partir do primeiro efeito não aplicado")
    c.add_argument("id"); c.add_argument("--campaign"); c.set_defaults(f=_wrap(cmd_retry))
    c = t.add_parser("inspect", help="o que um evento aplicou, o que falta e o que é incerto (auditoria)")
    c.add_argument("id"); c.add_argument("--campaign"); c.set_defaults(f=_wrap(cmd_inspect))
    c = t.add_parser("upcoming", help="eventos pendentes/falhos em ordem de processamento")
    c.add_argument("--limit", type=int, default=20); c.add_argument("--campaign"); c.set_defaults(f=_wrap(cmd_upcoming))
    k = sp.add_parser("fact", help="fatos do mundo e conhecimento das entidades").add_subparsers(dest="kcmd", required=True)
    c = k.add_parser("add", help="registra fato (verdadeiro ou boato falso)")
    c.add_argument("id"); c.add_argument("text"); c.add_argument("--false", action="store_true", help="boato falso")
    c.add_argument("--public", action="store_true"); c.add_argument("--about"); c.add_argument("--campaign"); c.set_defaults(f=_wrap(cmd_fact_add))
    c = k.add_parser("learn", help="entidade passa a saber/acreditar/suspeitar (fonte obrigatória)")
    c.add_argument("who"); c.add_argument("fact"); c.add_argument("--status", choices=list(KNOW_RANK), default="sabe")
    c.add_argument("--source", required=True); c.add_argument("--confidence", type=float, default=1.0); c.add_argument("--campaign")
    c.set_defaults(f=_wrap(cmd_learn))
    c = k.add_parser("knows", help="o que uma entidade sabe/acredita/suspeita")
    c.add_argument("who"); c.add_argument("--all", action="store_true", help="lista também o que desconhece"); c.add_argument("--campaign")
    c.set_defaults(f=_wrap(cmd_knows))
    c = sp.add_parser("res", help="saldos de recursos")
    c.add_argument("who", nargs="?"); c.add_argument("--campaign"); c.set_defaults(f=_wrap(cmd_res))
