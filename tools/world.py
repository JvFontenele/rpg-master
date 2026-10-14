"""Mundo vivo: grafo de relações, linha do tempo estruturada, agenda de NPCs e verificador.

Arquivos (append-only, nunca reescritos):
  state/relations.jsonl  {"op":"add"|"end", "id":"rel-0001", ...} e {"op":"feel", "from", "to", "dim", "delta", ...}
                         (sentimentos direcionais; valor atual = soma do histórico, ver sim.feelings)
  state/events.jsonl     {"id":"evt-0001", "date":..., "session":..., ...}
Relações implícitas vêm do frontmatter (location, faction, giver, region, parent, home) e não são duplicadas.
"""
import json, re
from pathlib import Path

from rpg import CAMPS, ROOT, camp_dir, die, now, open_session, read_meta, sessions

ENTITY_SUBDIRS = {"npc-": "npcs", "loc-": "locations", "quest-": "quests", "pc-": "characters"}
ID_ANY = re.compile(r"\b(?:npc|loc|quest|pc|fac|poi|map|evt|rel)-[a-z0-9][a-z0-9-]*\b")
IMPLICIT = {"location": "esta_em", "faction": "membro_de", "giver": "oferecida_por",
            "region": "fica_em", "parent": "fica_em", "home": "mora_em"}


def known_section(text):
    """Conteúdo de '## Conhecido pelos jogadores' (sem comentários HTML)."""
    m = re.search(r"^## Conhecido pelos jogadores[^\n]*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    return re.sub(r"<!--.*?-->", "", m.group(1), flags=re.S).strip() if m else ""


def section(text, title):
    m = re.search(rf"^## {re.escape(title)}[^\n]*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    return re.sub(r"<!--.*?-->", "", m.group(1), flags=re.S).strip() if m else ""


def _jsonl(path):
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _append(path, rec):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


class Ctx:
    """Visão carregada de uma campanha: entidades, facções, relações, eventos."""

    def __init__(self, cdir):
        self.dir = Path(cdir)
        self.ent = {}  # id -> {"meta", "known", "text", "path"}
        for sub in ENTITY_SUBDIRS.values():
            for p in sorted((self.dir / sub).glob("*.md")):
                t = p.read_text(encoding="utf-8")
                meta = read_meta(p)
                self.ent[meta.get("id", p.stem)] = {"meta": meta, "known": known_section(t), "text": t, "path": p}
        world = (self.dir / "world.md").read_text(encoding="utf-8") if (self.dir / "world.md").exists() else ""
        self.factions = sorted(set(re.findall(r"\bfac-[a-z0-9][a-z0-9-]*\b", world)))
        kp = self.dir / "state" / "knowledge.md"
        self.knowledge = kp.read_text(encoding="utf-8") if kp.exists() else ""
        import sim
        self.events = sim.fold_events(_jsonl(self.dir / "state" / "events.jsonl"))
        self.rel_records = _jsonl(self.dir / "state" / "relations.jsonl")

    def name(self, eid):
        e = self.ent.get(eid)
        return e["meta"].get("name", eid) if e else eid

    def exists(self, eid, map_ids=(), poi_ids=()):
        if eid in self.ent or eid in self.factions or eid in map_ids or eid in poi_ids:
            return True
        if eid.startswith("evt-"):
            return any(e["id"] == eid for e in self.events)
        if eid.startswith("rel-"):
            return any(r["id"] == eid for r in self.rel_records)
        return False

    def is_public(self, eid):
        """Os jogadores sabem que isso existe? Mesma autoridade do resto do projeto."""
        if eid.startswith("pc-"):
            return eid in self.ent
        e = self.ent.get(eid)
        if e:
            return bool(e["known"]) and e["meta"].get("status") != "oculta"
        if eid.startswith("fac-"):
            public_text = self.knowledge + "\n".join(x["known"] for x in self.ent.values())
            return eid in public_text
        return False

    def relations(self, include_ended=False):
        cur = {}
        for r in self.rel_records:
            if r["op"] == "add":
                cur[r["id"]] = dict(r, ended=None)
            elif r["op"] == "end" and r["id"] in cur:
                cur[r["id"]]["ended"] = r.get("session") or r.get("t")
        rels = [r for r in cur.values() if include_ended or not r["ended"]]
        for eid, e in sorted(self.ent.items()):  # implícitas do frontmatter
            for key, typ in IMPLICIT.items():
                tgt = e["meta"].get(key)
                if tgt and ID_ANY.fullmatch(tgt):
                    rels.append({"id": f"(frontmatter:{key})", "a": eid, "b": tgt, "type": typ,
                                 "secret": False, "implicit": True, "ended": None})
        return rels


# ---------------------------------------------------------------- relações
def _next_id(records, prefix):
    n = max([int(r["id"].split("-")[1]) for r in records if r.get("id", "").startswith(prefix)] or [0])
    return f"{prefix}{n + 1:04d}"


def _session_no(cdir):
    op = open_session(cdir)
    return int(read_meta(op)["session"]) if op else (len(sessions(cdir)) or None)


def _require(ctx, ids):
    import maps
    mids, pids = maps.campaign_ids(ctx.dir)
    bad = [i for i in ids if i and not ctx.exists(i, mids, pids)]
    if bad:
        die(f"referência inexistente: {', '.join(bad)} (crie a entidade antes ou corrija o id)")


def cmd_rel_add(a):
    d = camp_dir(a.campaign)
    ctx = Ctx(d)
    _require(ctx, [a.a, a.b])
    if a.a == a.b:
        die("relação de uma entidade com ela mesma")
    for r in ctx.relations():  # direcional: A→B e B→A são relações distintas; sem direção, o par é o mesmo
        same = (r["a"], r["b"]) == (a.a, a.b) or ({r["a"], r["b"]} == {a.a, a.b} and not (r.get("directed") or a.directed))
        if not r.get("implicit") and same and r["type"] == a.type:
            die(f"relação já existe: {r['id']}")
    rid = _next_id(ctx.rel_records, "rel-")
    # direcional descreve atitude interior (admira, tem ciúme de): do mestre, salvo --public explícito
    secret = a.secret or (a.directed and not a.public)
    rec = {"op": "add", "id": rid, "a": a.a, "b": a.b, "type": a.type, "note": a.note, "secret": secret,
           "session": _session_no(d), "t": now().isoformat()}
    if a.directed:
        rec["directed"] = True
    if a.public:  # intenção EXPLÍCITA (desde 3.1.1): distingue de registros antigos sem marcação (ambíguos)
        rec["public"] = True
    _append(d / "state" / "relations.jsonl", rec)
    print(f"Relação {rid}: {a.a} —{a.type}→ {a.b}" + (" (direcional)" if a.directed else "") + (" [MESTRE]" if secret else ""))


def cmd_rel_end(a):
    d = camp_dir(a.campaign)
    ctx = Ctx(d)
    if not any(r["id"] == a.id for r in ctx.relations()):
        die(f"relação ativa '{a.id}' não encontrada")
    _append(d / "state" / "relations.jsonl", {"op": "end", "id": a.id, "note": a.note,
                                                "session": _session_no(d), "t": now().isoformat()})
    print(f"Relação {a.id} encerrada (histórico preservado)")


def rel_query(ctx, eid, typ=None, with_prefix=None, public=False, include_ended=False):
    out = []
    for r in ctx.relations(include_ended):
        if eid not in (r["a"], r["b"]):
            continue
        other = r["b"] if r["a"] == eid else r["a"]
        if typ and r["type"] != typ or with_prefix and not other.startswith(with_prefix):
            continue
        if public and (r.get("secret") or not ctx.is_public(other) or not ctx.is_public(eid)):
            continue
        out.append((r, other))
    return out


def cmd_rel_query(a):
    import sim
    ctx = Ctx(camp_dir(a.campaign))
    rows = rel_query(ctx, a.id, a.type, a.with_prefix, a.public, a.all)
    feel = {} if a.public else {k: v for k, v in sim.feelings(ctx.rel_records).items() if a.id in k}  # números: só mestre
    if not rows and not feel:
        print("Nenhuma relação encontrada.")
    for r, other in rows:
        arrow = f"{r['a']} —{r['type']}→ {r['b']}" + (" (direcional)" if r.get("directed") else "")
        extra = " [MESTRE]" if r.get("secret") else ""
        extra += f" (encerrada: {r['ended']})" if r.get("ended") else ""
        note = f" — {r['note']}" if r.get("note") else ""
        print(f"{r['id']:22} {arrow} [{ctx.name(other)}]{extra}{note}")


    for (frm, to), dims in sorted(feel.items()):  # cada sentido é independente: nunca há reciprocidade implícita
        vals = ", ".join(f"{k} {v:+d}" for k, v in sorted(dims.items()))
        print(f"{'sentimento':22} {frm} ⇢ {to} [{ctx.name(to if frm == a.id else frm)}] [MESTRE]: {vals}")


def rel_audit(ctx):
    """Somente leitura. Relações direcionais (atitudes interiores) → [(classe, registro, exposta_agora)].
    classe: 'privada' (secret), 'publica-explicita' (marcada 'public' com --public, desde 3.1.1) ou 'ambigua'
    (direcional, não secreta e sem marcação: anterior à 3.1.1 — a intenção não pode ser deduzida do registro)."""
    out = []
    for r in ctx.relations(include_ended=True):
        if r.get("implicit") or not r.get("directed"):
            continue
        cls = "privada" if r.get("secret") else ("publica-explicita" if r.get("public") else "ambigua")
        exposed = cls != "privada" and not r.get("ended") and ctx.is_public(r["a"]) and ctx.is_public(r["b"])
        out.append((cls, r, exposed))
    return out


def cmd_rel_audit(a):
    ctx = Ctx(camp_dir(a.campaign))
    rows = rel_audit(ctx)
    n = {k: sum(1 for c, _, _ in rows if c == k) for k in ("privada", "publica-explicita", "ambigua")}
    print(f"Relações direcionais: {len(rows)} — privadas {n['privada']}, públicas explícitas {n['publica-explicita']}, "
          f"ambíguas {n['ambigua']} (somente leitura: nada foi alterado)")
    for cls, r, exposed in rows:
        if cls == "privada":
            continue
        where = ("EXPOSTA AGORA em 'world rel --public', '/consultar relacoes' e no painel 'Relacionados' do mapa do jogador"
                 if exposed else "não exposta agora (encerrada ou alguma entidade ainda desconhecida dos jogadores); "
                                 "fica exposta quando as duas forem conhecidas")
        print(f"[{cls}] {r['id']} {r['a']} —{r['type']}→ {r['b']} (sessão {r.get('session', '?')}): {where}")
    if n["ambigua"]:
        print("Ambíguas não são migradas automaticamente: decida caso a caso (encerrar com 'world rel-end' e recriar "
              "com --secret, ou manter pública).")


def cmd_feel(a):
    """Muda o que A sente por B (direcional), via evento: idempotente por --op, com causa e recuperação."""
    import sim
    sim.load_modules()
    d = camp_dir(a.campaign)
    try:
        w = sim.World(d)
        f = {"type": "relation.feel", "from": a.frm, "to": a.to, "dim": a.dim, "delta": a.delta,
             "reason": a.reason, "declared": a.declared}
        gk = f"feel:{a.op}"
        if gk not in {e.get("gen_key") for e in w.events()}:  # recusa antes de criar o evento
            sim.check_feel(w, f)
            if a.cause and not w.event(a.cause):
                raise sim.SimError(f"causa '{a.cause}' não existe")
        e, done = sim.apply_now(w, {"type": "relacao.sentimento", "text": f"{a.frm} ⇢ {a.to}: {a.dim} {a.delta:+d} — {a.reason}",
                                    "entities": [a.frm, a.to], "hidden": True, "cause": a.cause, "effects": [f]}, gk,
                              declared=bool(a.declared))
    except sim.SimError as ex:
        die(str(ex))
    r = e["result"]["efeitos"][0]
    print(f"{e['id']}{' (já aplicado; nada repetido)' if done else ''}: {r['de']} ⇢ {r['para']} {r['dim']} agora {r['valor']:+d} [MESTRE]")


def cmd_move(a):
    """Registra o deslocamento DECLARADO de um PJ (ou decidido para um NPC): o frontmatter 'location' é o que as
    testemunhas (knowledge.witness) usam. Evento auditável, idempotente por --op."""
    import sim
    sim.load_modules()
    d = camp_dir(a.campaign)
    try:
        w = sim.World(d)
        gk = f"move:{a.op}"
        if gk not in {e.get("gen_key") for e in w.events()}:
            if not a.id.startswith(("pc-", "npc-")) or a.id not in w.ctx.ent:
                raise sim.SimError(f"'{a.id}' não é um PJ/NPC existente")
            if not a.loc.startswith("loc-") or not w.exists(a.loc):
                raise sim.SimError(f"local '{a.loc}' não existe")
            if a.cause and not w.event(a.cause):
                raise sim.SimError(f"causa '{a.cause}' não existe")
            if a.id.startswith("npc-") and "npc.free" in sim.CONDITIONS:
                ok, why = sim.check(w, {"type": "npc.free", "npc": a.id})
                if not ok:
                    raise sim.SimError(why)
        hidden = a.hidden or (not a.public and not a.id.startswith("pc-"))
        e, done = sim.apply_now(w, {"type": "movimento", "text": f"{a.id} vai para {a.loc}" + (f": {a.reason}" if a.reason else ""),
                                    "entities": [a.id], "loc": a.loc, "hidden": hidden, "cause": a.cause,
                                    "effects": [{"type": "entity.set", "id": a.id, "field": "location", "value": a.loc}]}, gk,
                              declared=a.id.startswith("pc-"))  # deslocamento do PJ = o que o jogador declarou
    except sim.SimError as ex:
        die(str(ex))
    r = e["result"]["efeitos"][0]
    print(f"{e['id']}{' (já aplicado; nada repetido)' if done else ''}: {r['entidade']} {r['de']} → {r['para']}"
          + (" [MESTRE]" if e.get("hidden") else ""))


# ---------------------------------------------------------------- linha do tempo
def cmd_event_add(a):
    d = camp_dir(a.campaign)
    ctx = Ctx(d)
    ents = [e.strip() for e in (a.entities or "").split(",") if e.strip()]
    import sim
    sig = gk = None
    if a.op is not None:  # --op: repetir a MESMA operação não registra outro evento
        if not a.op.strip():
            die("--op vazio")
        # --date como digitado; sem --date = "agora" do relógio na primeira chamada (repetição após avanço reconhecida)
        gk, sig = f"event:{a.op}", sim.op_sig({k: getattr(a, k) for k in ("text", "date", "type", "cause", "loc", "consequences",
                                                                          "prev", "corrects", "reason", "session", "hidden")}
                                                | {"entities": ents})
        try:
            prev = sim.op_prev(ctx.events, gk, sig)
        except sim.SimError as e:
            die(str(e))
        if prev:
            print(f"Operação '{a.op}' já registrou {prev['id']} [{prev.get('date')}] (status: {prev['status']}) — nada novo registrado")
            return
    _require(ctx, ents + [a.loc, a.prev, a.corrects, a.cause])
    if a.corrects and not a.reason:
        die("uma correção precisa de --reason (o evento antigo é preservado)")
    clock = sim.load_clock(d, required=False)
    if not a.date and not clock:
        die("informe --date (ou inicie o relógio com 'rpg.py time init')")
    at = clock["now"] if clock else None
    rec = {"id": _next_id(ctx.events, "evt-"), "date": a.date or sim.fmt(at, sim.cal_of(clock)), "due": at,
           "type": a.type, "cause": a.cause, "session": a.session or _session_no(d),
           "text": a.text, "entities": ents, "loc": a.loc, "consequences": a.consequences,
           "prev": a.prev, "corrects": a.corrects, "reason": a.reason, "hidden": a.hidden, "gen_key": gk, "op_sig": sig,
           "t": now().isoformat()}
    sim.append_jsonl(d / "state" / "events.jsonl", {k: v for k, v in rec.items() if v not in (None, "", [], False)})
    print(f"Evento {rec['id']} [{a.date}] registrado" + (" [MESTRE]" if a.hidden else ""))


def _touches(e, eid):
    """O evento envolve a entidade? (participantes, local ou alvo de algum efeito)"""
    if eid in e.get("entities", []) or e.get("loc") == eid or eid in (e.get("quest"), e.get("faction"), e.get("npc")):
        return True
    return any(eid in [v for v in f.values() if isinstance(v, str)] for f in e.get("effects", []))


def filter_events(ctx, a):
    import sim
    evs = ctx.events
    clock = sim.load_clock(ctx.dir, required=False)
    cal = sim.cal_of(clock) if clock else sim.DEFAULT_CAL
    if getattr(a, "entity", None):
        evs = [e for e in evs if _touches(e, a.entity)]
    if getattr(a, "session", None):
        evs = [e for e in evs if e.get("session") == a.session]
    if getattr(a, "type", None):
        evs = [e for e in evs if e.get("type") == a.type]
    if getattr(a, "status", None):
        evs = [e for e in evs if e.get("status") == a.status]
    if getattr(a, "cause", None):
        evs = [e for e in evs if e.get("cause") == a.cause]
    if getattr(a, "from_", None):
        t0 = sim.parse_when(a.from_, cal, clock["now"] if clock else 0)
        evs = [e for e in evs if e.get("due") is not None and e["due"] >= t0]
    if getattr(a, "to", None):
        t1 = sim.parse_when(a.to, cal, clock["now"] if clock else 0)
        evs = [e for e in evs if e.get("due") is not None and e["due"] <= t1]
    if getattr(a, "public", False):
        evs = [e for e in evs if is_public_event(e)]
    return evs


def is_public_event(e):
    """Única regra do que os jogadores podem ver de um evento: já aconteceu e não é segredo. Usada por TODA saída pública
    (timeline --public, mapas dos jogadores). Agendados, cancelados, falhos e ocultos nunca saem."""
    return not e.get("hidden") and e.get("status", "processado") == "processado" and e.get("type") != "tempo"


def cmd_event_list(a):
    ctx = Ctx(camp_dir(a.campaign))
    evs = filter_events(ctx, a)
    corrected = {e["corrects"]: e["id"] for e in ctx.events if e.get("corrects")}
    if not evs:
        print("Nenhum evento estruturado (eventos antigos podem estar em timeline.md).")
    for e in evs:
        tag = " [MESTRE]" if e.get("hidden") else ""
        tag += f" (corrigido por {corrected[e['id']]})" if e["id"] in corrected and not a.public else ""
        refs = ", ".join(e.get("entities", []))
        if not a.public:
            tag += f" ({e['status']})" if e.get("status", "processado") != "processado" else ""
            tag += f" ← {e['cause']}" if e.get("cause") else ""
            tag += f" [{e['type']}]" if e.get("type") and e.get("type") != "registro" else ""
        print(f"{e['id']} [{e.get('date', '?')}] s{e.get('session', '?')}{tag} {e.get('text', '')}"
              + (f" | local: {e['loc']}" if e.get("loc") else "") + (f" | envolvidos: {refs}" if refs else "")
              + (f" | consequências: {e['consequences']}" if e.get("consequences") else "")
              + (f" | após {e['prev']}" if e.get("prev") else "") + (f" | corrige {e['corrects']}" if e.get("corrects") else ""))


def _line(e):
    res = e.get("result") or {}
    eff = "; ".join(str(r) for r in res.get("efeitos", [])) if isinstance(res, dict) else ""
    why = (res.get("condicoes") or res.get("erro") or "") if isinstance(res, dict) else ""
    return (f"{e['id']} [{e.get('date', '?')}] ({e.get('status', 'processado')}) [{e.get('type', 'registro')}] {e.get('text', '')}"
            + (" [MESTRE]" if e.get("hidden") else "") + (f" → {eff}" if eff else "") + (f" | {why}" if why else ""))


def cmd_chain(a):
    ctx = Ctx(camp_dir(a.campaign))
    by = {e["id"]: e for e in ctx.events}
    if a.id not in by:
        die(f"evento '{a.id}' não existe")
    def ancestry(eid, seen):
        up, cur = [], by.get(eid)
        while cur and cur["id"] not in seen:
            seen.add(cur["id"])
            up.append(cur)
            cur = by.get(cur.get("cause") or cur.get("prev") or "")
        return up

    seen = set()
    up = ancestry(a.id, seen)
    print("Causas (do mais antigo ao evento):")
    for i, e in enumerate(reversed(up)):
        print("  " * i + "└ " + _line(e))
    # decisões guardam também a ORIGEM do conhecimento que usaram ('sources'): cadeias adicionais, sem repetir eventos
    pend = [(e["id"], s) for e in up for s in e.get("sources", [])]
    while pend:
        who, s = pend.pop(0)
        extra = ancestry(s, seen)
        if extra:
            print(f"Origem do conhecimento usado em {who}:")
            for i, e in enumerate(reversed(extra)):
                print("  " * i + "└ " + _line(e))
            pend += [(e["id"], x) for e in extra for x in e.get("sources", [])]
    print("Consequências:")

    seen_down = set()

    def down(eid, depth, seen=seen_down):
        for e in ctx.events:
            if (eid in (e.get("cause"), e.get("prev"), e.get("corrects")) or eid in e.get("sources", [])) and e["id"] not in seen:
                seen.add(e["id"])
                print("  " * depth + "└ " + _line(e))
                down(e["id"], depth + 1)
    down(a.id, 1)


def cmd_why(a):
    ctx = Ctx(camp_dir(a.campaign))
    by = {e["id"]: e for e in ctx.events}
    hits = [e for e in ctx.events if _touches(e, a.id) and e.get("status", "processado") == "processado"][-a.limit:]
    if not hits:
        print(f"Nenhum evento registrado afetou {a.id}.")
    for e in reversed(hits):
        print(_line(e))
        c, n, chain = by.get(e.get("cause") or ""), 0, [e]
        while c and n < 8:
            print("    causa: " + _line(c))
            chain.append(c)
            c, n = by.get(c.get("cause") or ""), n + 1
        for s in dict.fromkeys(s for x in chain for s in x.get("sources", [])):
            if s in by:
                print("    origem do conhecimento: " + _line(by[s]))


# ---------------------------------------------------------------- agenda
def cmd_agenda(a):
    ctx = Ctx(camp_dir(a.campaign))
    npcs = [(k, v) for k, v in ctx.ent.items() if k.startswith("npc-") and (not a.npc or k == a.npc)]
    for eid, e in npcs:
        m = e["meta"]
        print(f"## {eid} — {m.get('name', '?')} | status: {m.get('status', '?')} | local: {m.get('location', '?')}"
              f" | facção: {m.get('faction', '-')}")
        for title in ("Objetivos", "Agenda"):
            body = section(e["text"], title)
            if body:
                print(f"  {title}: " + body.replace("\n", "\n    "))
        rels = rel_query(ctx, eid)
        if rels:
            print("  Relações: " + "; ".join(f"{r['type']}→{o}" for r, o in rels))
    if not npcs:
        print("Nenhum NPC.")


# ---------------------------------------------------------------- verificador (somente leitura)
def verify(d):
    import maps
    ctx = Ctx(d)
    probs = []
    mids, pids = maps.campaign_ids(d)

    def chk(eid, where):
        if eid and ID_ANY.fullmatch(eid) and not ctx.exists(eid, mids, pids):
            probs.append(f"[referência quebrada] {where}: '{eid}' não existe")

    for eid, e in sorted(ctx.ent.items()):
        rel = e["path"].relative_to(d)
        for key in ("location", "faction", "giver", "region", "parent", "home"):
            chk(e["meta"].get(key), f"{rel} ({key})")
        if eid.startswith("npc-") and e["meta"].get("location", "").startswith("loc-"):
            loc = ctx.ent.get(e["meta"]["location"])
            if loc and loc["meta"].get("status") in ("destruido", "destruída", "destruido", "removido"):
                probs.append(f"[local incompatível] {eid} está em {loc['meta']['id']}, marcado como {loc['meta']['status']}")
        if eid.startswith("quest-") and e["meta"].get("status") in ("ativa", "oculta"):
            q_loc = e["meta"].get("location")
            if q_loc and q_loc not in ctx.ent:
                probs.append(f"[missão sem local] {eid} aponta para local removido '{q_loc}'")
    # duplicatas por nome
    seen = {}
    for eid, e in sorted(ctx.ent.items()):
        key = (eid.split("-")[0], maps.slug(e["meta"].get("name", "")))
        if key[1] and key in seen:
            probs.append(f"[duplicado?] '{e['meta'].get('name')}' em {seen[key]} e {eid}")
        seen.setdefault(key, eid)
    # relações e eventos
    for r in ctx.relations(include_ended=True):
        if not r.get("implicit"):
            chk(r["a"], r["id"]); chk(r["b"], r["id"])
    for r in ctx.rel_records:
        if r.get("op") == "feel":
            chk(r["from"], f"sentimento {r.get('key')}"); chk(r["to"], f"sentimento {r.get('key')}")
    ids = [e["id"] for e in ctx.events]
    for i, e in enumerate(ctx.events):
        for x in e.get("entities", []) + [e.get("loc"), e.get("prev"), e.get("corrects")]:
            chk(x, e["id"])
        if e.get("prev") in ids and ids.index(e["prev"]) > i:
            probs.append(f"[linha do tempo] {e['id']} referencia {e['prev']}, registrado depois")
        for x in e.get("entities", []):
            st = ctx.ent.get(x, {}).get("meta", {}).get("status", "")
            if st.startswith("mort") and not e.get("hidden") and "morre" not in e["text"].lower() \
                    and not any(w in e["text"].lower() for w in ("morte", "morto", "morta", "corpo", "funeral")):
                probs.append(f"[continuidade] {e['id']} envolve {x}, que está com status '{st}'")
    # presentes na cena vs local
    cur = (d / "state" / "current.md")
    if cur.exists():
        t = cur.read_text(encoding="utf-8")
        here = re.search(r"\*\*Local atual:\*\*(.*)", t)
        pres = re.search(r"\*\*Personagens presentes:\*\*(.*)", t)
        here_ids = set(ID_ANY.findall(here.group(1))) if here else set()
        for nid in sorted(set(ID_ANY.findall(pres.group(1))) if pres else ()):
            m = ctx.ent.get(nid, {}).get("meta", {})
            if nid.startswith(("npc-", "pc-")) and here_ids and m.get("location") and m["location"] not in here_ids:
                probs.append(f"[local incompatível] {nid} está presente na cena, mas o frontmatter diz location: {m['location']}"
                             + (" — testemunhas (knowledge.witness) usam o frontmatter: atualize com 'world move'" if nid.startswith("pc-") else ""))
            if m.get("status", "").startswith("mort"):
                probs.append(f"[continuidade] {nid} presente na cena mas status '{m['status']}'")
    # segredos em arquivos públicos
    secret_lines = []
    for p in sorted((d / "secrets").glob("*.md")) if (d / "secrets").exists() else []:
        secret_lines += [ln.strip("-* ").strip() for ln in p.read_text(encoding="utf-8").splitlines()
                         if len(ln.strip("-* ").strip()) >= 40 and not ln.startswith("#")]
    public = {"journal.md": (d / "journal.md"), "state/knowledge.md": d / "state" / "knowledge.md"}
    for eid, e in ctx.ent.items():
        public[f"{e['path'].relative_to(d)} (Conhecido pelos jogadores)"] = e["known"]
    for name, src in public.items():
        txt = src.read_text(encoding="utf-8") if isinstance(src, Path) and src.exists() else (src if isinstance(src, str) else "")
        for ln in secret_lines:
            if ln in txt:
                probs.append(f"[segredo exposto] {name} contém trecho de secrets/: '{ln[:60]}…'")
    probs += maps.verify_maps(d, ctx)
    import sim
    probs += sim.verify(d)
    return probs


def cmd_verify(a):
    d = camp_dir(a.campaign)
    probs = verify(d)
    print("\n".join(probs) if probs else f"OK: nenhuma inconsistência encontrada em {d.name}")
    print("(somente leitura — nada foi alterado)")


def register(sp):
    w = sp.add_parser("world", help="relações, linha do tempo, agenda e verificação").add_subparsers(dest="wcmd", required=True)
    c = w.add_parser("rel-add", help="registra relação A —tipo→ B")
    c.add_argument("a"); c.add_argument("b"); c.add_argument("type")
    c.add_argument("--note", default="")
    g = c.add_mutually_exclusive_group()
    g.add_argument("--secret", action="store_true")
    g.add_argument("--public", action="store_true", help="com --directed: os jogadores podem ver (padrão: só o mestre)")
    c.add_argument("--directed", action="store_true", help="A→B não implica B→A (ex.: admira, tem ciúme de); nasce secreta")
    c.add_argument("--campaign"); c.set_defaults(f=cmd_rel_add)
    c = w.add_parser("rel-audit", help="auditoria SOMENTE LEITURA de relações direcionais: privadas, públicas explícitas e ambíguas")
    c.add_argument("--campaign"); c.set_defaults(f=cmd_rel_audit)
    c = w.add_parser("feel", help="muda o que A sente por B (direcional; dimensões em sim.FEEL_DIMS)")
    c.add_argument("frm", metavar="de"); c.add_argument("to", metavar="para"); c.add_argument("dim")
    c.add_argument("delta", type=int, help="variação, ex.: +10 ou -5"); c.add_argument("--reason", required=True)
    c.add_argument("--op", required=True, help="id único da ação (evita repetir)"); c.add_argument("--cause")
    c.add_argument("--declared", action="store_true", help="obrigatório para sentimento de PJ: o jogador declarou/autorizou")
    c.add_argument("--campaign"); c.set_defaults(f=cmd_feel)
    c = w.add_parser("rel-end", help="encerra relação (sem apagar)")
    c.add_argument("id"); c.add_argument("--note", default=""); c.add_argument("--campaign"); c.set_defaults(f=cmd_rel_end)
    c = w.add_parser("rel", help="consulta relações de uma entidade")
    c.add_argument("id"); c.add_argument("--type"); c.add_argument("--with", dest="with_prefix", help="ex.: npc- quest-")
    c.add_argument("--public", action="store_true"); c.add_argument("--all", action="store_true"); c.add_argument("--campaign")
    c.set_defaults(f=cmd_rel_query)
    c = w.add_parser("move", help="registra o deslocamento de um PJ (declarado pelo jogador) ou NPC — base das testemunhas")
    c.add_argument("id"); c.add_argument("loc"); c.add_argument("--op", required=True); c.add_argument("--reason", default="")
    c.add_argument("--cause"); g = c.add_mutually_exclusive_group()
    g.add_argument("--hidden", action="store_true"); g.add_argument("--public", action="store_true")
    c.add_argument("--campaign"); c.set_defaults(f=cmd_move)
    c = w.add_parser("event-add", help="registra evento na linha do tempo (append-only)")
    c.add_argument("text"); c.add_argument("--date", help="data dentro do mundo (padrão: relógio da campanha)")
    c.add_argument("--type", default="registro"); c.add_argument("--cause", help="evt-* que causou este")
    c.add_argument("--entities", help="ids separados por vírgula"); c.add_argument("--loc")
    c.add_argument("--consequences", default=""); c.add_argument("--prev"); c.add_argument("--corrects")
    c.add_argument("--reason", default=""); c.add_argument("--session", type=int)
    c.add_argument("--op", help="id da operação: repetir com os mesmos dados não registra outro evento")
    c.add_argument("--hidden", action="store_true"); c.add_argument("--campaign")
    c.set_defaults(f=cmd_event_add)
    c = w.add_parser("timeline", help="lista eventos (filtros: entidade, sessão, período, tipo, status, causa)")
    c.add_argument("--entity"); c.add_argument("--session", type=int); c.add_argument("--type"); c.add_argument("--status")
    c.add_argument("--cause"); c.add_argument("--from", dest="from_", help="A/M/D [HH:MM] ou +-"); c.add_argument("--to")
    c.add_argument("--public", action="store_true"); c.add_argument("--campaign")
    c.set_defaults(f=cmd_event_list)
    c = w.add_parser("chain", help="cadeia causal de um evento (causas e consequências)")
    c.add_argument("id"); c.add_argument("--campaign"); c.set_defaults(f=cmd_chain)
    c = w.add_parser("why", help="explica as mudanças de uma entidade (eventos que a afetaram e suas causas)")
    c.add_argument("id"); c.add_argument("--limit", type=int, default=10); c.add_argument("--campaign"); c.set_defaults(f=cmd_why)
    c = w.add_parser("agenda", help="objetivos, local, relações e próximas ações dos NPCs")
    c.add_argument("--npc"); c.add_argument("--campaign"); c.set_defaults(f=cmd_agenda)
    c = w.add_parser("verify", help="verificador de continuidade (somente leitura)")
    c.add_argument("--campaign"); c.set_defaults(f=cmd_verify)
