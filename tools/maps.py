"""Mapas procedurais só com stdlib: mundo/região, cidade, masmorra (multinível) e batalha.

Fonte de verdade: campaigns/<id>/maps/<map-id>.json (dados do MESTRE).
Derivados (regenerados a cada alteração; não editar à mão):
  maps/mestre/   SVGs completos + index.html
  maps/jogador/  SVGs e index.html gerados a partir de uma cópia FILTRADA dos dados —
                 o que não foi revelado nem chega a ser escrito.
Reprodutibilidade: mesma seed + parâmetros + versão do gerador → mesmo JSON (exceto
campos 'updated' e ids que precisaram de sufixo por colidirem com ids já existentes na campanha).
"""
import copy, heapq, html, json, math, os, random, re, shutil, subprocess, tempfile, unicodedata
from collections import deque
from pathlib import Path

GEN_VERSION = 1
STATUSES = ("rumor", "conhecido", "visitado")
TYPES = ("mundo", "regiao", "cidade", "masmorra", "batalha")
AC = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"  # índice de área → caractere
N4 = ((0, -1), (-1, 0), (1, 0), (0, 1))
N8 = ((-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1))


class MapError(Exception):
    pass


def slug(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def now_iso():
    import datetime as dt
    return dt.datetime.now().astimezone().replace(microsecond=0).isoformat()


# ---------------------------------------------------------------- ruído e nomes
def _h(ix, iy, seed):
    n = (ix * 374761393 + iy * 668265263 + seed * 1442695041) & 0xFFFFFFFF
    n = ((n ^ (n >> 13)) * 1274126177) & 0xFFFFFFFF
    return ((n ^ (n >> 16)) & 0xFFFFFF) / 0xFFFFFF


def noise(x, y, seed):
    ix, iy = math.floor(x), math.floor(y)
    fx, fy = x - ix, y - iy
    sx, sy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)
    a, b = _h(ix, iy, seed), _h(ix + 1, iy, seed)
    c, d = _h(ix, iy + 1, seed), _h(ix + 1, iy + 1, seed)
    top, bot = a + (b - a) * sx, c + (d - c) * sx
    return top + (bot - top) * sy


def fbm(x, y, seed, octaves=5):
    t = norm = 0.0
    amp, f = 1.0, 1.0
    for o in range(octaves):
        t += amp * noise(x * f, y * f, seed + o * 101)
        norm += amp
        amp *= 0.5
        f *= 2
    return t / norm


_A = "Al Bel Bra Cal Cor Dor Est Fal Fen Gal Gil Gar Lu Mar Mon Nor Pe Quel Ri Ro Sa Sel Ser Tal Tor Tri Ur Val Ver Za Ar Bor Car Duna Esm Ita Jun Lis Mira Oli Pra Rav Sol Tam Vi".split()
_B = "a e i o an ar el en or im os ur".split()
_C = "dor nia mar val vel rim beira mont gal lar res tos ena ara ela uz ago edo ira ona ano".split()


def gen_name(rng, used):
    for _ in range(500):
        n = rng.choice(_A) + (rng.choice(_B) if rng.random() < 0.35 else "") + rng.choice(_C)
        n = n[0].upper() + n[1:].lower()
        if slug(n) not in used:
            used.add(slug(n))
            return n
    raise MapError("acabaram os nomes livres")


def _uniq(base, used, suffix=""):
    """Id estável: base; se já usado na campanha, base-sufixo, depois base-2..."""
    cand = base
    if cand in used and suffix:
        cand = f"{base}-{suffix}"
    k = 2
    while cand in used:
        cand = f"{base}-{k}"
        k += 1
    used.add(cand)
    return cand


def _uname(points, kind, name, level=None):
    """Evita dois locais do mesmo tipo com o mesmo nome no mapa/nível (ex.: 'Ponte de X II')."""
    base, k = name, 2
    while any(p["kind"] == kind and slug(p["name"]) == slug(name) and p.get("level") == level for p in points):
        name = f"{base} {['', '', 'II', 'III', 'IV', 'V', 'VI', 'VII', 'VIII', 'IX', 'X'][k] if k <= 10 else k}"
        k += 1
    return name


def _q(vals, q):
    s = sorted(vals)
    return s[int(q * (len(s) - 1))] if s else 0.0


def astar(start, goal, W, H, cost, nei=N8, min_cost=1.0):
    """cost(x,y) → custo ≥ min_cost, ou None se intransponível. Retorna lista de células."""
    if start == goal:
        return [start]
    g = {start: 0.0}
    came = {}
    pq = [(0.0, 0.0, start)]
    while pq:
        _, gc, cur = heapq.heappop(pq)
        if cur == goal:
            path = [goal]
            while path[-1] != start:
                path.append(came[path[-1]])
            return path[::-1]
        if gc > g[cur]:
            continue
        for dx, dy in nei:
            nx, ny = cur[0] + dx, cur[1] + dy
            if not (0 <= nx < W and 0 <= ny < H):
                continue
            c = cost(nx, ny)
            if c is None:
                continue
            ng = gc + c * (1.4142 if dx and dy else 1.0)
            if ng < g.get((nx, ny), 1e18):
                g[(nx, ny)] = ng
                came[(nx, ny)] = cur
                heapq.heappush(pq, (ng + math.hypot(goal[0] - nx, goal[1] - ny) * min_cost, ng, (nx, ny)))
    return None


def bfs(starts, W, H, ok, nei=N4):
    seen = set(starts)
    dq = deque(starts)
    while dq:
        x, y = dq.popleft()
        for dx, dy in nei:
            n = (x + dx, y + dy)
            if 0 <= n[0] < W and 0 <= n[1] < H and n not in seen and ok(*n):
                seen.add(n)
                dq.append(n)
    return seen


def _farthest(cands, k, rng, score=None):
    """Amostragem 'mais distante primeiro', ponderada por score."""
    if not cands:
        return []
    score = score or (lambda c: 1.0)
    chosen = [max(cands, key=lambda c: (score(c), -c[1], -c[0]))]
    while len(chosen) < k:
        best = max(cands, key=lambda c: (min(math.hypot(c[0] - o[0], c[1] - o[1]) for o in chosen) * (0.4 + score(c)), -c[1], -c[0]))
        if best in chosen:
            break
        chosen.append(best)
    return chosen


# ================================================================= MUNDO / REGIÃO
BIOME = {"~": ("Oceano", "#2f5d8a"), "-": ("Mar raso", "#4f86b5"), "L": ("Lago", "#5b9bd0"),
         ".": ("Planície", "#b9c98c"), "f": ("Floresta", "#5f8f4e"), "h": ("Colinas", "#a9a072"),
         "m": ("Montanhas", "#8a7f72"), "*": ("Picos nevados", "#f2f2f2"), "d": ("Deserto", "#e3cf8f"),
         "s": ("Pântano", "#6f8a6a"), "t": ("Tundra", "#c8d0c4"), "?": ("Inexplorado", "#e9dfc6")}
WATER = set("~-L")
CLIMATE = {"temperado": (0, 0), "frio": (-0.3, 0.02), "quente": (0.2, 0.0), "arido": (0.12, -0.2), "umido": (0.05, 0.18)}
WORLD_DEF = dict(w=160, h=100, continents=2, land=0.38, regions=6, density=6.0, ruins=4,
                 climate="temperado", islands=0.4, km=10.0)
LAND_COST = {".": 1, "f": 1.5, "h": 2.5, "m": 6, "*": 9, "d": 1.6, "s": 2.5, "t": 1.8}
SITE = {".": 1, "f": .7, "h": .6, "d": .25, "s": .2, "t": .3, "m": .05, "*": 0}


def _continents(seed, W0, H0, n, islands):
    rng = random.Random(seed * 7 + 1)
    cs = [(rng.uniform(.28, .72) * W0, rng.uniform(.3, .7) * H0,
           rng.uniform(.30, .42) * min(W0, H0) * (1.3 if n == 1 else 1), 1.0) for _ in range(n)]
    cs += [(rng.uniform(.08, .92) * W0, rng.uniform(.1, .9) * H0, rng.uniform(.04, .08) * min(W0, H0), .7)
           for _ in range(int(islands * 10))]
    return cs


def _terrain(seed, p, origin, step, base, thr):
    W, H = p["w"], p["h"]
    W0, H0 = base
    cs = _continents(seed, W0, H0, p["continents"], p["islands"])
    f = 6.0 / min(W0, H0)
    dt, dm = CLIMATE[p["climate"]]
    E, M, T = [[0.0] * W for _ in range(H)], [[0.0] * W for _ in range(H)], [[0.0] * W for _ in range(H)]
    for y in range(H):
        wy = origin[1] + (y + .5) * step
        for x in range(W):
            wx = origin[0] + (x + .5) * step
            m = max(wt * (1 - math.hypot(wx - cx, (wy - cy) * 1.2) / r) for cx, cy, r, wt in cs)
            ridge = 1 - abs(2 * fbm(wx * f * 1.7, wy * f * 1.7, seed + 31, 4) - 1)
            e = 0.55 * fbm(wx * f, wy * f, seed, 5) + 0.75 * m + 0.2 * ridge * ridge * max(m, 0)
            edge = min(wx, W0 - wx, (wy if wy < H0 / 2 else H0 - wy) * W0 / H0) / W0
            e -= max(0.0, 0.07 - edge) * 5
            E[y][x] = e
            M[y][x] = fbm(wx * f * .8, wy * f * .8, seed + 77, 4) + dm
            T[y][x] = 1 - abs(wy / H0 - .5) * 1.7 + dt
    if thr is None:
        flat = [v for r in E for v in r]
        sea = _q(flat, 1 - p["land"])
        land = [v for v in flat if v >= sea]
        thr = dict(sea=sea, shallow=sea - 0.035, hills=_q(land, .80), mount=_q(land, .92), snow=_q(land, .98))
    return E, M, T, thr


def _biomes(E, M, T, thr):
    rows = []
    for y, r in enumerate(E):
        row = []
        for x, e in enumerate(r):
            if e < thr["sea"]:
                ch = "-" if e > thr["shallow"] else "~"
            elif e >= thr["snow"]:
                ch = "*"
            elif e >= thr["mount"]:
                ch = "m"
            elif e >= thr["hills"]:
                ch = "h"
            else:
                t, m = T[y][x] - (e - thr["sea"]) * 0.5, M[y][x]
                low = e < thr["sea"] + (thr["hills"] - thr["sea"]) * .25
                ch = "t" if t < .2 else "d" if m < .4 and t > .45 else "s" if m > .6 and low else "f" if m > .5 else "."
            row.append(ch)
        rows.append(row)
    return rows


def _hydro(E, M, B, W, H):
    """Priority-flood: preenche depressões (lagos) e dá a direção de drenagem até o mar/borda."""
    F = [r[:] for r in E]
    done = [[False] * W for _ in range(H)]
    parent, order, pq = {}, [], []
    for y in range(H):
        for x in range(W):
            if B[y][x] in "~-" or x in (0, W - 1) or y in (0, H - 1):
                done[y][x] = True
                heapq.heappush(pq, (E[y][x], y, x))
    while pq:
        e, y, x = heapq.heappop(pq)
        order.append((x, y))
        for dx, dy in N8:
            nx, ny = x + dx, y + dy
            if 0 <= nx < W and 0 <= ny < H and not done[ny][nx]:
                done[ny][nx] = True
                F[ny][nx] = max(E[ny][nx], F[y][x] + 1e-6)
                parent[(nx, ny)] = (x, y)
                heapq.heappush(pq, (F[ny][nx], ny, nx))
    land = [(x, y) for y in range(H) for x in range(W) if B[y][x] not in WATER]
    if not land:
        return [], set()
    depth = (_q([E[y][x] for x, y in land], .5) - _q([E[y][x] for x, y in land], .0)) * 0.08
    cand = {(x, y) for x, y in land if F[y][x] - E[y][x] > depth}
    lakes = set()
    for c in sorted(cand):
        if c in lakes:
            continue
        comp = bfs([c], W, H, lambda x, y: (x, y) in cand)
        if len(comp) >= 3:
            lakes |= comp
    acc = {c: max(M[c[1]][c[0]], 0.05) for c in land}
    for c in reversed(order):
        if c in acc and c in parent and parent[c] in acc:
            acc[parent[c]] += acc[c]
    thr = max(_q(list(acc.values()), .975), 5.0)
    river = {c for c, v in acc.items() if v >= thr and c not in lakes}
    has_up = {parent[c] for c in river if c in parent}
    rivers, taken = [], set()
    for src in sorted(c for c in river if c not in has_up):
        path, c = [src], src
        while c in parent:
            c = parent[c]
            path.append(c)
            if c in taken or B[c[1]][c[0]] in "~-" or c not in acc:
                break
        if len(path) >= 6:
            taken.update(path)
            w = 1 + (acc.get(path[-2], 0) > thr * 4) + (acc.get(path[-2], 0) > thr * 12)
            rivers.append({"w": w, "pts": [list(p) for p in path]})
    for x, y in lakes:
        B[y][x] = "L"
    return rivers, {tuple(p) for r in rivers for p in r["pts"][:-1]}


def _landmasses(B, W, H):
    lm, k = {}, 0
    for y in range(H):
        for x in range(W):
            if B[y][x] not in WATER and (x, y) not in lm:
                for c in bfs([(x, y)], W, H, lambda a, b: B[b][a] not in WATER, N8):
                    lm[c] = k
                k += 1
    return lm


def _grow_regions(B, W, H, seeds, river_cells):
    """Dijkstra a partir das capitais; montanhas e rios custam mais → fronteiras naturais."""
    owner, dist, pq = {}, {}, []
    for i, (x, y) in enumerate(seeds):
        dist[(x, y)] = 0.0
        heapq.heappush(pq, (0.0, i, x, y))
    while pq:
        d, i, x, y = heapq.heappop(pq)
        if (x, y) in owner:
            continue
        owner[(x, y)] = i
        for dx, dy in N4:
            nx, ny = x + dx, y + dy
            if 0 <= nx < W and 0 <= ny < H and (nx, ny) not in owner:
                ch = B[ny][nx]
                c = 6.0 if ch in WATER else LAND_COST[ch] + (2.0 if (nx, ny) in river_cells else 0)
                if d + c < dist.get((nx, ny), 1e18):
                    dist[(nx, ny)] = d + c
                    heapq.heappush(pq, (d + c, i, nx, ny))
    return owner


def _site_score(B, W, H, x, y, river_cells):
    ch = B[y][x]
    if ch in WATER:
        return 0.0
    s = SITE[ch]
    near = [B[y + dy][x + dx] for dx, dy in N8 if 0 <= x + dx < W and 0 <= y + dy < H]
    if (x, y) in river_cells or any((x + dx, y + dy) in river_cells for dx, dy in N4):
        s += .6
    if any(c in "~-" for c in near):
        s += .5
    elif "L" in near:
        s += .3
    return s


def _roads(points, B, W, H, lm, rng):
    nodes = [p for p in points if p["kind"] in ("capital", "cidade", "vila", "fortaleza")]
    used, paths = set(), []

    def cost(x, y):
        ch = B[y][x]
        if ch in WATER:
            return None
        return 0.5 if (x, y) in used else LAND_COST[ch]

    groups = {}
    for p in nodes:
        groups.setdefault(lm.get((p["x"], p["y"])), []).append(p)
    for g in sorted(groups, key=lambda k: (k is None, k)):
        ns = groups[g]
        if g is None or len(ns) < 2:
            continue
        edges = sorted((math.hypot(a["x"] - b["x"], a["y"] - b["y"]), i, j)
                       for i, a in enumerate(ns) for j, b in enumerate(ns) if i < j)
        uf = list(range(len(ns)))

        def find(i):
            while uf[i] != i:
                uf[i] = uf[uf[i]]
                i = uf[i]
            return i
        mst = []
        for d, i, j in edges:
            if find(i) != find(j):
                uf[find(i)] = find(j)
                mst.append((d, i, j))
        extra = [(d, i, j) for d, i, j in edges if (d, i, j) not in mst and d < 18 and rng.random() < .15][:max(1, len(ns) // 5)]
        for d, i, j in sorted(mst + extra):
            path = astar((ns[i]["x"], ns[i]["y"]), (ns[j]["x"], ns[j]["y"]), W, H, cost, N8, 0.5)
            if path:
                used.update(path)
                paths.append([list(c) for c in path])
    return paths


def gen_world(seed, p, used_ids, used_names, parent=None):
    rng = random.Random(seed)
    W, H = p["w"], p["h"]
    if parent:
        pp = parent["generator"]["params"]
        base, thr = tuple(pp["_base"]), pp["_thr"]
        x0, y0, x1, y1 = p["bbox"]
        step = (x1 - x0) / W
        H = p["h"] = max(10, round((y1 - y0) / step))
        origin = (pp["_origin"][0] + x0 * pp["_step"], pp["_origin"][1] + y0 * pp["_step"])
        step *= pp["_step"]
        for k in ("continents", "islands", "climate"):
            p[k] = pp[k]
    else:
        base, thr, origin, step = (W, H), None, (0.0, 0.0), 1.0
    E, M, T, thr = _terrain(seed if not parent else parent["generator"]["seed"], p, origin, step, base, thr)
    p.update(_base=list(base), _thr=thr, _origin=list(origin), _step=step)
    B = _biomes(E, M, T, thr)
    rivers, river_cells = _hydro(E, M, B, W, H)
    lm = _landmasses(B, W, H)
    land = [(x, y) for y in range(H) for x in range(W) if B[y][x] not in WATER]
    if not land:
        raise MapError("o mapa ficou sem terra; aumente --land ou mude a seed")
    score = {c: _site_score(B, W, H, c[0], c[1], river_cells) * (0.8 + 0.4 * _h(c[0], c[1], seed)) for c in land}
    areas, points = [], []
    pre = "" if not parent else parent["id"].removeprefix("map-")

    def add_point(kind, name, x, y, **kw):
        name = _uname(points, kind, name)
        pid = _uniq("poi-" + slug(name), used_ids, pre)
        pt = dict(id=pid, name=name, kind=kind, x=x, y=y, **kw)
        points.append(pt)
        return pt

    # --- regiões políticas
    if parent:
        prow, pare = parent["layers"]["regions"], parent["areas"]
        px0, py0 = p["bbox"][0], p["bbox"][1]
        sx = (p["bbox"][2] - p["bbox"][0]) / W
        region_rows, idx_map = [], {}
        for y in range(H):
            row = []
            for x in range(W):
                ch = prow[min(len(prow) - 1, int(py0 + (y + .5) * sx))][min(len(prow[0]) - 1, int(px0 + (x + .5) * sx))]
                if B[y][x] in WATER or ch == ".":
                    row.append(".")
                    continue
                if ch not in idx_map:
                    idx_map[ch] = AC[len(idx_map)]
                    areas.append(copy.deepcopy(pare[AC.index(ch)]))
                row.append(idx_map[ch])
            region_rows.append("".join(row))
        for pt in parent["points"]:  # herda locais do mapa-pai (mesmo id = mesmo lugar)
            if p["bbox"][0] <= pt["x"] < p["bbox"][2] and p["bbox"][1] <= pt["y"] < p["bbox"][3]:
                q = copy.deepcopy(pt)
                q["x"] = min(W - 1, int((pt["x"] + .5 - px0) / sx))
                q["y"] = min(H - 1, int((pt["y"] + .5 - py0) / sx))
                q.pop("link_map", None)
                if B[q["y"]][q["x"]] in WATER:  # resolução maior pode cair na água: acha terra próxima
                    near = min(land, key=lambda c: (math.hypot(c[0] - q["x"], c[1] - q["y"]), c))
                    q["x"], q["y"] = near
                points.append(q)
                used_names.add(slug(q["name"]))
        n_new = round(p["density"] * len(land) / 1000 * 0.35)
        kinds_new = ["vila"] * n_new
    else:
        cand = sorted(sorted(land, key=lambda c: -score[c])[:max(50, len(land) // 4)])
        caps = _farthest(cand, p["regions"], rng, lambda c: score[c])
        owner = _grow_regions(B, W, H, caps, river_cells)
        region_rows = ["".join(AC[owner[(x, y)]] if B[y][x] not in WATER and (x, y) in owner else "." for x in range(W))
                       for y in range(H)]
        titles = ["Reino de", "Ducado de", "Marca de", "Terras de", "Principado de", "Condado de"]
        for i, (cx, cy) in enumerate(caps):
            nm = gen_name(rng, used_names)
            aid = "reg-" + slug(nm)
            areas.append(dict(id=aid, name=f"{titles[i % len(titles)]} {nm}", kind="regiao", faction=None))
            add_point("capital", nm, cx, cy, region=aid)
        n_new = round(p["density"] * len(land) / 1000)
        kinds_new = ["cidade" if i < n_new // 4 else "vila" for i in range(n_new)]

    def region_of(x, y):
        ch = region_rows[y][x]
        return areas[AC.index(ch)]["id"] if ch != "." else None

    for pt in points:
        pt.setdefault("region", region_of(pt["x"], pt["y"]))
    # --- assentamentos
    dmin = max(3.0, math.sqrt(len(land) / (len(kinds_new) + len(points) + 1)) * 0.7)
    taken = [(pt["x"], pt["y"]) for pt in points]
    for c in sorted(land, key=lambda c: (-score[c], c)):
        if not kinds_new:
            break
        if score[c] < .4 or any(math.hypot(c[0] - t[0], c[1] - t[1]) < dmin for t in taken):
            continue
        add_point(kinds_new.pop(0), gen_name(rng, used_names), c[0], c[1], region=region_of(*c))
        taken.append(c)
    # --- fortalezas nas fronteiras / passagens
    if not parent:
        border = sorted(c for c in land if any(0 <= c[0] + dx < W and 0 <= c[1] + dy < H
                        and region_rows[c[1] + dy][c[0] + dx] not in (".", region_rows[c[1]][c[0]]) for dx, dy in N4))
        border = [c for c in border if all(math.hypot(c[0] - t[0], c[1] - t[1]) >= 4 for t in taken)]
        for c in _farthest(border, min(p["regions"], len(border)), rng, lambda c: 1 + (B[c[1]][c[0]] in "hm")):
            add_point("fortaleza", "Forte " + gen_name(rng, used_names), c[0], c[1], region=region_of(*c))
            taken.append(c)
    # --- ruínas (metade secretas)
    remote = sorted(c for c in land if B[c[1]][c[0]] not in "*" and all(math.hypot(c[0] - t[0], c[1] - t[1]) >= dmin for t in taken))
    for i, c in enumerate(_farthest(remote, min(p["ruins"], len(remote)), rng, lambda c: 1.0) if p["ruins"] else []):
        add_point("ruina", "Ruínas de " + gen_name(rng, used_names), c[0], c[1], region=region_of(*c),
                  secret=(i % 2 == 1), gm_note="ruína escondida; os PJs só a encontram explorando ou por pista" if i % 2 else "")
    for pt in points:
        if pt["kind"] in ("capital", "cidade", "vila") and any(
                0 <= pt["x"] + dx < W and 0 <= pt["y"] + dy < H and B[pt["y"] + dy][pt["x"] + dx] in "~-" for dx, dy in N8):
            pt["port"] = True
    # --- estradas, pontes, passagens
    roads = _roads(points, B, W, H, lm, rng)
    near = lambda c: min((pt for pt in points if pt["kind"] in ("capital", "cidade", "vila")),
                         key=lambda pt: (math.hypot(pt["x"] - c[0], pt["y"] - c[1]), pt["id"]), default=None)
    seen_b, seen_m = set(), set()
    for path in roads:
        crossed = False
        for i, c in enumerate(map(tuple, path)):
            if c in river_cells and c not in seen_b and not any(math.hypot(c[0] - b[0], c[1] - b[1]) < 4 for b in seen_b):
                seen_b.add(c)
                ref = near(c)
                add_point("ponte", f"Ponte de {ref['name'] if ref else gen_name(rng, used_names)}", c[0], c[1], region=region_of(*c))
            if B[c[1]][c[0]] in "m*" and not crossed and not any(math.hypot(c[0] - b[0], c[1] - b[1]) < 6 for b in seen_m):
                crossed = True
                seen_m.add(c)
                add_point("passagem", "Passo " + gen_name(rng, used_names), c[0], c[1], region=region_of(*c))
    layers = {"biomes": ["".join(r) for r in B], "regions": region_rows, "rivers": rivers, "roads": roads}
    return areas, points, layers


# ================================================================= CIDADE
CITY_DEF = dict(w=96, h=72, districts=6, gates=4, river=None, taverns=3, temples=2, m=4.0)
DIST_KINDS = ["Mercado", "Docas", "Cidade Alta", "Guarda", "Artesãos", "Templo", "Cidade Baixa", "Curtidores", "Estudantes"]
DIRS = ["Leste", "Sudeste", "Sul", "Sudoeste", "Oeste", "Noroeste", "Norte", "Nordeste"]
ANIMAIS = "Javali Corvo Galo Lobo Urso Cervo Bode Texugo Grifo Peixe Cão Gato".split()
ADJ = "Torto Dourado Bêbado Manco Velho Cinzento Risonho Afogado Valente Sonolento".split()
OFICIOS = "Ferreiros Tecelões Mercadores Pedreiros Alquimistas Carpinteiros Ourives Escribas".split()


def gen_city(seed, p, used_ids, used_names, map_slug):
    rng = random.Random(seed)
    W, H = p["w"], p["h"]
    if p["river"] is None:
        p["river"] = rng.random() < .5
    cx, cy = W / 2 + rng.uniform(-3, 3), H / 2 + rng.uniform(-3, 3)
    R, ph = min(W, H) * 0.36, rng.random() * 10

    def rad(a):
        return R * (0.86 + 0.28 * fbm(math.cos(a) * 1.3 + ph, math.sin(a) * 1.3 + ph, seed, 3))
    inside = {(x, y) for y in range(H) for x in range(W)
              if math.hypot(x + .5 - cx, y + .5 - cy) < rad(math.atan2(y + .5 - cy, x + .5 - cx))}
    G = [[" "] * W for _ in range(H)]
    river = set()
    if p["river"]:
        y0, amp, fr = rng.uniform(.3, .7) * H, rng.uniform(2, 6), rng.uniform(.04, .09)
        for x in range(W):
            yc = int(y0 + amp * math.sin(x * fr + ph))
            for dy in (-1, 0, 1):
                if 0 <= yc + dy < H:
                    river.add((x, yc + dy))
    wall = {c for c in inside if any((c[0] + dx, c[1] + dy) not in inside for dx, dy in N4)}
    interior = inside - wall
    for x, y in river:
        G[y][x] = "~"
    for x, y in wall:
        G[y][x] = "~" if (x, y) in river else "W"
    # praça central
    pw, ph2 = 6, 5
    plaza = {(x, y) for y in range(int(cy) - ph2 // 2, int(cy) + ph2 - ph2 // 2)
             for x in range(int(cx) - pw // 2, int(cx) + pw - pw // 2) if (x, y) in interior and (x, y) not in river}
    for x, y in plaza:
        G[y][x] = "p"
    # portões e vias principais
    gates = []
    for k in range(p["gates"]):
        a = 2 * math.pi * k / p["gates"] + rng.uniform(-.3, .3)
        tx, ty = cx + math.cos(a) * rad(a), cy + math.sin(a) * rad(a)
        g = min(sorted(c for c in wall if c not in river), key=lambda c: math.hypot(c[0] - tx, c[1] - ty))
        if all(math.hypot(g[0] - o[0], g[1] - o[1]) > 6 for o, _ in gates):
            gates.append((g, a))
    for (g, _) in gates:
        G[g[1]][g[0]] = "G"
    gate_cells = {g for g, _ in gates}
    pc = (int(cx), int(cy))

    def cost_main(x, y):
        if (x, y) in wall and (x, y) not in gate_cells:
            return None
        return 12.0 if (x, y) in river else (0.5 if G[y][x] in "Rp" else 1.0)
    exits = []
    for g, a in gates:
        for c in astar(g, pc, W, H, cost_main, N4, 0.5) or []:
            if G[c[1]][c[0]] in " ~":
                G[c[1]][c[0]] = "B" if c in river else "R"
        bx = min(W - 1, max(0, int(cx + math.cos(a) * W)))
        by = min(H - 1, max(0, int(cy + math.sin(a) * H)))
        t = (bx, by)
        path = astar(g, t, W, H, lambda x, y: None if (x, y) in inside and (x, y) not in gate_cells else (12.0 if (x, y) in river else 1.0), N4, 1.0)
        for c in path or []:
            if G[c[1]][c[0]] in " ~":
                G[c[1]][c[0]] = "B" if c in river else "R"
        if path:
            exits.append((path[-1], a))
    # bairros (Voronoi) + arrabaldes
    ic = sorted(interior - river)
    seeds = _farthest(ic, p["districts"], rng, lambda c: .5 + _h(c[0], c[1], seed))
    dist_of = {c: min(range(len(seeds)), key=lambda i: (math.hypot(c[0] - seeds[i][0], c[1] - seeds[i][1]), i)) for c in interior}
    road_out = {(x, y) for y in range(H) for x in range(W) if G[y][x] in "RB" and (x, y) not in inside}
    sub = {(x, y) for y in range(H) for x in range(W) if (x, y) not in inside and (x, y) not in river
           and math.hypot(x - cx, y - cy) < R * 1.45 and any(math.hypot(x - a, y - b) <= 3 for a, b in road_out)}
    # tipos de bairro
    cent = {}
    for c, i in dist_of.items():
        cent.setdefault(i, []).append(c)
    kinds = {}
    plaza_d = dist_of.get(pc, 0)
    kinds[plaza_d] = "Mercado"
    rest = [i for i in range(len(seeds)) if i not in kinds]
    if river and rest:
        i = max(rest, key=lambda i: (sum(1 for c in cent[i] if any((c[0] + dx, c[1] + dy) in river for dx, dy in N4)), -i))
        if any(any((c[0] + dx, c[1] + dy) in river for dx, dy in N4) for c in cent[i]):
            kinds[i] = "Docas"
            rest.remove(i)
    mid = lambda i: (sum(c[0] for c in cent[i]) / len(cent[i]), sum(c[1] for c in cent[i]) / len(cent[i]))
    if rest:
        i = max(rest, key=lambda i: (min(math.hypot(mid(i)[0] - g[0], mid(i)[1] - g[1]) for g, _ in gates), -i))
        kinds[i] = "Cidade Alta"
        rest.remove(i)
    if rest:
        i = min(rest, key=lambda i: (min(math.hypot(mid(i)[0] - g[0], mid(i)[1] - g[1]) for g, _ in gates), i))
        kinds[i] = "Guarda"
        rest.remove(i)
    others = [k for k in DIST_KINDS if k not in kinds.values()]
    for j, i in enumerate(rest):
        kinds[i] = others[j % len(others)]
    nomes = {"Mercado": "Bairro do Mercado", "Docas": "Docas", "Cidade Alta": "Cidade Alta", "Guarda": "Bairro da Guarda",
             "Artesãos": "Bairro dos Artesãos", "Templo": "Bairro do Templo", "Cidade Baixa": "Cidade Baixa",
             "Curtidores": "Bairro dos Curtidores", "Estudantes": "Bairro dos Estudantes"}
    areas = [dict(id=f"dist-{map_slug}-{slug(kinds[i])}", name=nomes[kinds[i]], kind="bairro", tipo=kinds[i]) for i in range(len(seeds))]
    if sub:
        areas.append(dict(id=f"dist-{map_slug}-arrabaldes", name="Arrabaldes", kind="bairro", tipo="Arrabaldes"))
    D = [["."] * W for _ in range(H)]
    for (x, y), i in dist_of.items():
        D[y][x] = AC[i]
    for x, y in sub:
        D[y][x] = AC[len(seeds)]
    # ruas secundárias em grade irregular por bairro
    grid_par = {i: (rng.randint(5, 7), rng.randint(0, 6), rng.randint(0, 6)) for i in range(len(seeds))}
    for (x, y), i in dist_of.items():
        s, ox, oy = grid_par[i]
        if G[y][x] == " " and ((x - ox) % s == 0 or (y - oy) % s == 0) and (x, y) not in river:
            G[y][x] = "r"
    walk = set("rRBpG")
    # conectar componentes de rua soltos à rede principal
    for _ in range(200):
        net = bfs([gates[0][0]], W, H, lambda x, y: G[y][x] in walk)
        loose = sorted((x, y) for y in range(H) for x in range(W) if G[y][x] in walk and (x, y) not in net)
        if not loose:
            break
        path = astar(loose[0], min(sorted(net), key=lambda c: math.hypot(c[0] - loose[0][0], c[1] - loose[0][1])), W, H,
                     lambda x, y: None if G[y][x] == "W" else (12.0 if (x, y) in river else (0.5 if G[y][x] in walk else 1.0)), N4, 0.5)
        if not path:
            for x, y in bfs([loose[0]], W, H, lambda x, y: G[y][x] in walk):
                G[y][x] = " "
            continue
        for x, y in path:
            if G[y][x] in " ~":
                G[y][x] = "B" if (x, y) in river else "r"
    # construções
    for x, y in interior | sub:
        if G[y][x] == " ":
            if (x, y) in sub and not any(G[y + dy][x + dx] in walk for dx, dy in N8 if 0 <= x + dx < W and 0 <= y + dy < H):
                continue
            G[y][x] = "b"
    # pontos de interesse
    points = []
    pids_local = []

    def add(kind, name, cell, area_i=None, **kw):
        name = _uname(points, kind, name)
        pid = _uniq("poi-" + slug(name), used_ids, map_slug)
        aid = areas[area_i]["id"] if area_i is not None else None
        pt = dict(id=pid, name=name, kind=kind, x=cell[0], y=cell[1], **kw)
        if aid:
            pt["area"] = aid
        points.append(pt)
        pids_local.append(cell)
        return pt

    def spot(pref_kinds, near=None):
        cands = sorted((x, y) for (x, y) in interior if G[y][x] == "b" and any(G[y + dy][x + dx] in walk for dx, dy in N4)
                       and all(math.hypot(x - a, y - b) >= 4 for a, b in pids_local))
        pref = [c for c in cands if kinds.get(dist_of.get(c)) in pref_kinds] or cands
        if not pref:
            return None, None
        if near:
            pref = sorted(pref, key=lambda c: (math.hypot(c[0] - near[0], c[1] - near[1]), c))[:6]
        c = rng.choice(pref)
        ent = next((c[0] + dx, c[1] + dy) for dx, dy in N4 if G[c[1] + dy][c[0] + dx] in walk)
        return c, ent

    pcell = (int(cx), int(cy)) if pc in plaza else (sorted(plaza)[0] if plaza else pc)
    add("mercado", "Praça do Mercado", pcell, dist_of.get(pcell), entrance=list(pcell))
    for (g, a) in gates:
        d = DIRS[int(((a % (2 * math.pi)) + math.pi / 8) // (math.pi / 4)) % 8]
        add("portao", f"Portão {d}", g, None, entrance=list(g))
    for (e, a) in exits:
        d = DIRS[int(((a % (2 * math.pi)) + math.pi / 8) // (math.pi / 4)) % 8]
        add("saida", f"Estrada do {d}", e, None, entrance=list(e))
    plan = [("palacio", f"Paço de {gen_name(rng, used_names)}", ["Cidade Alta"], None),
            ("quartel", "Quartel da Guarda", ["Guarda"], gates[0][0]),
            ("estalagem", f"Estalagem {rng.choice(['do', 'da'])} {gen_name(rng, used_names)}", ["Guarda", "Mercado"], gates[-1][0])]
    plan += [("guilda", "Guilda dos Mercadores", ["Mercado"], None),
             ("guilda", f"Guilda dos {rng.choice(OFICIOS[1:])}", ["Artesãos", "Docas", "Curtidores"], None)]
    plan += [("templo", f"Templo de {gen_name(rng, used_names)}", ["Templo", "Cidade Alta", "Mercado"], None) for _ in range(p["temples"])]
    plan += [("taverna", f"Taverna do {rng.choice(ANIMAIS)} {rng.choice(ADJ)}", ["Docas", "Cidade Baixa", "Mercado", "Guarda"], None)
             for _ in range(p["taverns"])]
    if river:
        plan.append(("porto", "Cais", ["Docas"], None))
    for kind, name, pref, near in plan:
        while slug(name) in {slug(x["name"]) for x in points}:
            name += " Novo"
        c, ent = spot(pref, near)
        if c:
            add(kind, name, c, dist_of.get(c), entrance=list(ent))
    layers = {"cells": ["".join(r) for r in G], "districts": ["".join(r) for r in D]}
    return areas, points, layers


# ================================================================= MASMORRA
DUN_DEF = dict(w=40, h=30, levels=1, rooms=9, traps=2, encounters=2, m=1.5)
TRAPS = ["fosso oculto", "dardos na parede", "laje que desaba", "gás sonífero", "lâmina pendular"]
FEATS = ["altar rachado", "estátua sem rosto", "poço seco", "mural desbotado", "fonte de água escura", "jaula vazia"]


def gen_dungeon(seed, p, used_ids, map_slug):
    rng = random.Random(seed)
    W, H, L = p["w"], p["h"], p["levels"]
    levels, areas, points = [], [], []
    prev_down = None

    def pt(kind, name, lv, c, **kw):
        name = _uname(points, kind, name, lv + 1)
        pid = _uniq(f"poi-{map_slug}-l{lv + 1}-{slug(name)}", used_ids)
        points.append(dict(id=pid, name=name, kind=kind, level=lv + 1, x=c[0], y=c[1], **kw))

    for lv in range(L):
        G = [["#"] * W for _ in range(H)]
        A = [["."] * W for _ in range(H)]
        rooms, room_at = [], {}

        def free(x, y, w, h):
            if x < 1 or y < 1 or x + w > W - 1 or y + h > H - 1:
                return False
            return all(x + w + 1 < r["x"] or r["x"] + r["w"] + 1 < x or y + h + 1 < r["y"] or r["y"] + r["h"] + 1 < y for r in rooms)

        def add_room(x, y, w, h, secret=False):
            k = len(rooms) + 1
            r = dict(id=f"area-{map_slug}-l{lv + 1}-sala-{k}", name=f"Sala {k}" + (" (secreta)" if secret else ""),
                     kind="sala", level=lv + 1, x=x, y=y, w=w, h=h)
            if secret:
                r["secret"] = True
            rooms.append(r)
            for yy in range(y, y + h):
                for xx in range(x, x + w):
                    G[yy][xx] = "."
                    room_at[(xx, yy)] = k - 1
            return r

        if prev_down:  # a primeira sala fica sob a escada do nível de cima
            add_room(min(max(1, prev_down[0] - 2), W - 6), min(max(1, prev_down[1] - 2), H - 6), 5, 5)
        for _ in range(400):
            if len(rooms) >= p["rooms"]:
                break
            w, h = rng.randint(3, 8), rng.randint(3, 6)
            x, y = rng.randint(1, W - w - 2), rng.randint(1, H - h - 2)
            if free(x, y, w, h):
                add_room(x, y, w, h)
        if len(rooms) < 2:
            raise MapError("masmorra pequena demais para as salas; aumente --w/--h ou reduza --rooms")
        ctr = [(r["x"] + r["w"] // 2, r["y"] + r["h"] // 2) for r in rooms]
        n = len(rooms)
        edges = sorted((math.hypot(ctr[i][0] - ctr[j][0], ctr[i][1] - ctr[j][1]), i, j) for i in range(n) for j in range(i + 1, n))
        uf = list(range(n))

        def find(i):
            while uf[i] != i:
                i = uf[i]
            return i
        links = []
        for _, i, j in edges:
            if find(i) != find(j):
                uf[find(i)] = find(j)
                links.append((i, j))
        links += [(i, j) for _, i, j in edges if (i, j) not in links and rng.random() < .12][:2]
        doors, cors = {}, []
        deg = [0] * (n + 1)

        def carve(a, b, secret_target=None):
            """Corredor em L de a até b; porta onde o corredor encontra uma sala."""
            (ax, ay), (bx, by) = a, b
            sx, sy = (1 if bx >= ax else -1), (1 if by >= ay else -1)
            if rng.random() < .5:
                path = [(x, ay) for x in range(ax, bx, sx)] + [(bx, y) for y in range(ay, by + sy, sy)]
            else:
                path = [(ax, y) for y in range(ay, by, sy)] + [(x, by) for x in range(ax, bx + sx, sx)]
            new = []
            for k, c in enumerate(path):
                if c not in room_at and G[c[1]][c[0]] == "#":
                    G[c[1]][c[0]] = "."
                    new.append(c)
                if k and (path[k - 1] in room_at) != (c in room_at):
                    inner = c if c in room_at else path[k - 1]
                    dc = path[k - 1] if c in room_at else c
                    if dc not in room_at:
                        sec = secret_target is not None and room_at[inner] == secret_target
                        doors[dc] = doors.get(dc, False) or sec
            if new:
                cors.append(new)

        for i, j in links:
            deg[i] += 1
            deg[j] += 1
            carve(ctr[i], ctr[j])
        # sala secreta: só em rocha intacta (com margem), ligada por porta secreta
        for _ in range(300):
            w, h = rng.randint(3, 4), rng.randint(3, 4)
            x, y = rng.randint(2, W - w - 3), rng.randint(2, H - h - 3)
            if all(G[yy][xx] == "#" for yy in range(y - 2, y + h + 2) for xx in range(x - 2, x + w + 2)):
                si = len(rooms)
                add_room(x, y, w, h, secret=True)
                ctr.append((x + w // 2, y + h // 2))
                near = min(range(n), key=lambda i: (math.hypot(ctr[i][0] - ctr[si][0], ctr[i][1] - ctr[si][1]), i))
                deg[near] += 1
                deg[si] += 1
                carve(ctr[near], ctr[si], secret_target=si)
                break
        for i, r in enumerate(rooms):
            for yy in range(r["y"], r["y"] + r["h"]):
                for xx in range(r["x"], r["x"] + r["w"]):
                    A[yy][xx] = AC[i]
        level_areas = list(rooms)
        for k, cells in enumerate(cors):
            idx = len(rooms) + k
            if idx >= len(AC):
                raise MapError("corredores demais para um nível; reduza --rooms")
            level_areas.append(dict(id=f"area-{map_slug}-l{lv + 1}-corredor-{k + 1}", name=f"Corredor {k + 1}", kind="corredor", level=lv + 1))
            for x, y in cells:
                A[y][x] = AC[idx]
        areas += level_areas
        for c, sec in sorted(doors.items()):
            G[c[1]][c[0]] = "S" if sec else "+"
            if sec:
                pt("porta_secreta", "Porta secreta", lv, c, secret=True, gm_note="parede falsa; exige busca para encontrar")
        start = ctr[0]
        G[start[1]][start[0]] = "<"
        if lv == 0:
            pt("entrada", "Entrada", lv, start)
        else:
            pt("escada_sobe", f"Escada para o nível {lv}", lv, start, link={"level": lv, "x": start[0], "y": start[1]})
        dist = {start: 0}
        dq = deque([start])
        while dq:
            c = dq.popleft()
            for dx, dy in N4:
                nb = (c[0] + dx, c[1] + dy)
                if 0 <= nb[0] < W and 0 <= nb[1] < H and nb not in dist and G[nb[1]][nb[0]] in ".+<>":
                    dist[nb] = dist[c] + 1
                    dq.append(nb)
        normal = [i for i in range(n)]
        if lv < L - 1:
            far = max((i for i in normal if i and ctr[i] in dist), key=lambda i: (dist[ctr[i]], -i))
            prev_down = ctr[far]
            G[prev_down[1]][prev_down[0]] = ">"
            pt("escada_desce", f"Escada para o nível {lv + 2}", lv, prev_down, link={"level": lv + 2, "x": prev_down[0], "y": prev_down[1]})
        cor_cells = sorted(c for cells in cors for c in cells if G[c[1]][c[0]] == ".")
        for t in rng.sample(cor_cells, min(p["traps"], len(cor_cells))):
            pt("armadilha", "Armadilha: " + rng.choice(TRAPS), lv, t, secret=True, gm_note="dificuldade sugerida: Normal (ajuste ao sistema)")
        for i, r in enumerate(rooms):
            if i and (deg[i] == 1 or r.get("secret")):
                c = (r["x"] + r["w"] - 1, r["y"] + r["h"] - 1)
                if G[c[1]][c[0]] == ".":
                    pt("tesouro", f"Tesouro ({r['name']})", lv, c, secret=True, area=r["id"], gm_note="conteúdo a definir conforme o sistema")
        cand = [i for i in normal if i]
        for i in rng.sample(cand, min(p["encounters"], len(cand))):
            r = rooms[i]
            pt("encontro", f"Encontro ({r['name']})", lv, (r["x"], r["y"] + r["h"] // 2), secret=True, area=r["id"],
               gm_note="criaturas a definir pelo mestre conforme o sistema")
        for i in rng.sample(cand, min(2, len(cand))):
            r = rooms[i]
            c = (r["x"] + r["w"] // 2, r["y"])
            if G[c[1]][c[0]] == ".":
                pt("ponto", rng.choice(FEATS).capitalize() + f" ({r['name']})", lv, c, area=r["id"])
        levels.append({"grid": ["".join(r) for r in G], "areas": ["".join(r) for r in A]})
    return areas, points, {"levels": levels}



# ================================================================= BATALHA
BAT_DEF = dict(w=20, h=16, env="campo", m=1.5)
ENVS = ("floresta", "ruina", "interior", "caverna", "rua", "campo")
BLOCK = set("#S")


def _letters(i):
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def coord(x, y):
    return f"{_letters(x)}{y + 1}"


def gen_battle(seed, p, src_level=None, src_area=None):
    rng = random.Random(seed)
    W, H = p["w"], p["h"]
    G = [["."] * W for _ in range(H)]
    env = p["env"]
    if src_level is not None:  # recorte de uma sala da masmorra
        grid = src_level["grid"]
        x0, y0, x1, y1 = src_area
        W, H = p["w"], p["h"] = x1 - x0, y1 - y0
        G = [[{"<": ".", ">": "."}.get(grid[y][x], grid[y][x]) for x in range(x0, x1)] for y in range(y0, y1)]
    elif env == "floresta":
        for y in range(H):
            for x in range(W):
                n = noise(x / 3, y / 3, seed)
                G[y][x] = "#" if _h(x, y, seed) > .86 else ":" if n > .62 else "."
    elif env == "caverna":
        G = [["#" if rng.random() < .45 or x in (0, W - 1) or y in (0, H - 1) else "." for x in range(W)] for y in range(H)]
        for _ in range(4):
            G = [["#" if x in (0, W - 1) or y in (0, H - 1) or
                  sum(G[y + dy][x + dx] == "#" for dx, dy in N8) >= 5 else "." for x in range(W)] for y in range(H)]
    elif env == "interior":
        for y in range(H):
            for x in range(W):
                if x in (0, W - 1) or y in (0, H - 1):
                    G[y][x] = "#"
        G[H // 2][0] = "+"
        G[0][W // 2] = "+"
        for _ in range(max(2, W * H // 60)):
            x, y = rng.randint(2, W - 4), rng.randint(2, H - 3)
            G[y][x] = G[y][x + 1] = "c"
        for x, y in ((W // 3, H // 3), (2 * W // 3, H // 3), (W // 3, 2 * H // 3), (2 * W // 3, 2 * H // 3)):
            G[y][x] = "#"
    elif env == "rua":
        for x in range(W):
            for y in (0, 1, H - 2, H - 1):
                G[y][x] = "#"
        for x in range(2, W - 2, 5):
            G[1][x] = G[H - 2][x] = "+"
        for _ in range(W // 4):
            G[rng.randint(3, H - 4)][rng.randint(1, W - 2)] = "c"
    elif env == "ruina":
        for _ in range(W * H // 50):
            x, y, ln, hz = rng.randint(1, W - 2), rng.randint(1, H - 2), rng.randint(3, 6), rng.random() < .5
            for k in range(ln):
                xx, yy = (x + k, y) if hz else (x, y + k)
                if 0 <= xx < W and 0 <= yy < H and k != ln // 2:
                    G[yy][xx] = "#" if rng.random() < .7 else "c"
        for y in range(H):
            for x in range(W):
                if G[y][x] == "." and noise(x / 2.5, y / 2.5, seed + 3) > .7:
                    G[y][x] = ":"
    else:  # campo
        sx = rng.randint(W // 3, 2 * W // 3)
        for y in range(H):
            x = int(sx + 2 * math.sin(y / 3 + seed))
            if 0 <= x < W:
                G[y][x] = "~"
        for y in range(H):
            for x in range(W):
                if G[y][x] == "." and noise(x / 3, y / 3, seed + 5) > .66:
                    G[y][x] = ":"
                elif G[y][x] == "." and _h(x, y, seed + 9) > .95:
                    G[y][x] = "#"
    return G


def place_tokens(G, W, H, party, enemies, ambush, rng):
    ok = lambda x, y: G[y][x] not in BLOCK and G[y][x] != "~"
    free = sorted((x, y) for y in range(H) for x in range(W) if ok(x, y))
    xs = sorted(c[0] for c in free)
    if not xs:
        raise MapError("o mapa não tem células livres")
    lo, hi = _q(xs, .3), _q(xs, .7)  # grupo no terço esquerdo da área livre, inimigos no direito
    zone_a = [c for c in free if c[0] <= lo]
    zone_b = [c for c in free if c[0] >= hi and c[0] > lo]
    rng.shuffle(zone_a)
    rng.shuffle(zone_b)
    toks = []
    for i, pid in enumerate(party):
        if not zone_a:
            raise MapError("sem espaço livre para o grupo")
        x, y = zone_a.pop()
        toks.append(dict(id=f"tok-{len(toks) + 1}", name=pid, ref=pid, side="grupo", x=x, y=y))
    for name in enemies:
        if not zone_b:
            raise MapError("sem espaço livre para os inimigos")
        x, y = zone_b.pop()
        t = dict(id=f"tok-{len(toks) + 1}", name=name, side="inimigo", x=x, y=y)
        if ambush:
            t["secret"] = True
        toks.append(t)
    # garante caminho entre os lados (abre escombros em linha reta se necessário)
    if toks and any(t["side"] == "grupo" for t in toks) and any(t["side"] == "inimigo" for t in toks):
        a = next(t for t in toks if t["side"] == "grupo")
        reach = bfs([(a["x"], a["y"])], W, H, lambda x, y: G[y][x] not in BLOCK)
        for b in [t for t in toks if t["side"] == "inimigo" and (t["x"], t["y"]) not in reach]:
            n = max(abs(b["x"] - a["x"]), abs(b["y"] - a["y"]))
            for k in range(n + 1):
                x = round(a["x"] + (b["x"] - a["x"]) * k / n)
                y = round(a["y"] + (b["y"] - a["y"]) * k / n)
                if G[y][x] in BLOCK:
                    G[y][x] = ":"
            reach = bfs([(a["x"], a["y"])], W, H, lambda x, y: G[y][x] not in BLOCK)
    return toks


def parse_enemies(spec):
    out = []
    for part in [s.strip() for s in (spec or "").split(",") if s.strip()]:
        m = re.match(r"^(.*?)\s*[xX×](\d+)$", part)
        name, n = (m.group(1).strip(), int(m.group(2))) if m else (part, 1)
        out += [f"{name} {i + 1}" if n > 1 else name for i in range(n)]
    return out


# ================================================================= arquivos / campanha
def mdir(cdir):
    return Path(cdir) / "maps"


def load_all(cdir):
    d = mdir(cdir)
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(d.glob("map-*.json"))] if d.exists() else []


def load_map(cdir, mid):
    p = mdir(cdir) / f"{mid}.json"
    if not p.exists():
        raise MapError(f"mapa '{mid}' não existe (veja 'rpg.py map list')")
    return json.loads(p.read_text(encoding="utf-8"))


def save_map(cdir, m, reason):
    m["updated"] = {"at": now_iso(), "reason": reason}
    probs = validate(m, cdir)
    if probs:
        raise MapError("mapa inválido, nada foi gravado:\n  " + "\n  ".join(probs))
    mdir(cdir).mkdir(exist_ok=True)
    p = mdir(cdir) / f"{m['id']}.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(m, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(p)


def campaign_ids(cdir):
    mids, pids = set(), set()
    for m in load_all(cdir):
        mids.add(m["id"])
        pids.update(p["id"] for p in m.get("points", []))
        pids.update(a["id"] for a in m.get("areas", []))
    return mids, pids


def _used(cdir):
    ids, names = set(), set()
    for m in load_all(cdir):
        ids.add(m["id"])
        for e in m.get("points", []) + m.get("areas", []):
            ids.add(e["id"])
            names.add(slug(e["name"].split(" de ")[-1]))
    for sub in ("npcs", "locations", "quests", "characters"):
        for p in (Path(cdir) / sub).glob("*.md"):
            ids.add(p.stem)
    return ids, names


def _elems(m):
    return m.get("points", []) + m.get("areas", []) + m.get("layers", {}).get("tokens", [])


def find_elem(m, eid):
    return next((e for e in _elems(m) if e["id"] == eid), None)


# ================================================================= validação
def _passable(m, e):
    t, lay = m["type"], m["layers"]
    x, y = e["x"], e["y"]
    if t in ("mundo", "regiao"):
        return lay["biomes"][y][x] not in WATER or e["kind"] in ("ponte", "porto")
    if t == "cidade":
        return lay["cells"][y][x] in "bprRBG" or e["kind"] == "saida"
    if t == "masmorra":
        return lay["levels"][e["level"] - 1]["grid"][y][x] in ".+S<>"
    return lay["grid"][y][x] not in "#" or e.get("kind") == "aoe"


def validate(m, cdir=None):
    P = []
    for k in ("id", "campaign", "type", "name", "generator", "size", "areas", "points", "connections", "discovery", "layers"):
        if k not in m:
            P.append(f"campo obrigatório ausente: {k}")
    if P:
        return P
    if not re.fullmatch(r"map-[a-z0-9][a-z0-9-]*", m["id"]):
        P.append(f"id inválido: {m['id']}")
    if m["type"] not in TYPES:
        P.append(f"tipo inválido: {m['type']}")
    if cdir and m["campaign"] != Path(cdir).name:
        P.append(f"campaign '{m['campaign']}' ≠ pasta '{Path(cdir).name}'")
    W, H = m["size"]["w"], m["size"]["h"]
    lay = m["layers"]
    grids = {"mundo": ["biomes", "regions"], "regiao": ["biomes", "regions"], "cidade": ["cells", "districts"], "batalha": ["grid"]}.get(m["type"], [])
    rows_list = [lay[g] for g in grids] + [lv[k] for lv in lay.get("levels", []) for k in ("grid", "areas")]
    for rows in rows_list:
        if len(rows) != H or any(len(r) != W for r in rows):
            P.append("grade com dimensões diferentes de size.w×size.h")
            return P
    ids = [e["id"] for e in _elems(m)]
    for i in sorted({i for i in ids if ids.count(i) > 1}):
        P.append(f"id duplicado no mapa: {i}")
    names = [(e.get("kind"), slug(e["name"]), e.get("level", 0)) for e in m["points"]]
    for n in sorted({n for n in names if names.count(n) > 1}):
        P.append(f"local duplicado (mesmo tipo e nome): {n[1]}")
    for e in m["points"] + lay.get("tokens", []):
        if not (0 <= e["x"] < W and 0 <= e["y"] < H):
            P.append(f"{e['id']}: coordenada fora do mapa ({e['x']},{e['y']})")
        elif m["type"] == "masmorra" and not 1 <= e.get("level", 0) <= len(lay["levels"]):
            P.append(f"{e['id']}: nível inválido")
        elif not _passable(m, e):
            P.append(f"{e['id']}: posicionado em célula intransponível ({coord(e['x'], e['y'])})")
    for k, v in m["discovery"].items():
        if v not in STATUSES:
            P.append(f"discovery[{k}] com status inválido '{v}'")
        if k != m["id"] and k not in ids:
            P.append(f"discovery referencia elemento inexistente: {k}")
    # conectividade
    if m["type"] in ("mundo", "regiao"):
        road = {tuple(c) for path in lay["roads"] for c in path}
        lm = _landmasses([list(r) for r in lay["biomes"]], W, H)
        towns = [p for p in m["points"] if p["kind"] in ("capital", "cidade", "vila", "fortaleza")]
        by_lm = {}
        for t in towns:
            by_lm.setdefault(lm.get((t["x"], t["y"])), []).append(t)
        for g, ts in sorted(by_lm.items(), key=lambda kv: (kv[0] is None, kv[0] or 0)):
            if len(ts) < 2 or g is None:
                continue
            reach = bfs([(ts[0]["x"], ts[0]["y"])], W, H, lambda x, y: (x, y) in road, N8)
            for t in ts[1:]:
                if (t["x"], t["y"]) not in reach:
                    P.append(f"{t['id']} sem estrada até {ts[0]['id']} (mesma massa de terra)")
    elif m["type"] == "cidade":
        cells = lay["cells"]
        gates = [p for p in m["points"] if p["kind"] == "portao"]
        if gates:
            reach = bfs([(gates[0]["x"], gates[0]["y"])], W, H, lambda x, y: cells[y][x] in "rRBpG")
            for p in m["points"]:
                ent = tuple(p.get("entrance") or (p["x"], p["y"]))
                if ent not in reach and not any((p["x"] + dx, p["y"] + dy) in reach for dx, dy in N4 + ((0, 0),)):
                    P.append(f"{p['id']} inacessível pelas ruas a partir de {gates[0]['id']}")
    elif m["type"] == "masmorra":
        lvls = lay["levels"]
        for i, lv in enumerate(lvls):
            g = lv["grid"]
            st = [(x, y) for y in range(H) for x in range(W) if g[y][x] == "<"]
            if len(st) != 1:
                P.append(f"nível {i + 1}: precisa de exatamente uma entrada/escada de subida '<'")
                continue
            reach = bfs(st, W, H, lambda x, y: g[y][x] in ".+S<>")
            for a in m["areas"]:
                if a.get("level") == i + 1:
                    ch = AC[[b for b in m["areas"] if b.get("level") == i + 1].index(a)]
                    cells = [(x, y) for y in range(H) for x in range(W) if lv["areas"][y][x] == ch]
                    if cells and not any(c in reach for c in cells):
                        P.append(f"{a['id']} inalcançável a partir da entrada do nível {i + 1}")
            downs = [(x, y) for y in range(H) for x in range(W) if g[y][x] == ">"]
            if i < len(lvls) - 1:
                if len(downs) != 1 or downs[0] not in reach:
                    P.append(f"nível {i + 1}: escada de descida ausente ou inalcançável")
                elif lvls[i + 1]["grid"][downs[0][1]][downs[0][0]] != "<":
                    P.append(f"nível {i + 1}→{i + 2}: escadas desalinhadas em {coord(*downs[0])}")
    elif m["type"] == "batalha":
        g = lay["grid"]
        toks = lay.get("tokens", [])
        if toks:
            reach = bfs([(toks[0]["x"], toks[0]["y"])], W, H, lambda x, y: g[y][x] not in BLOCK)
            for t in toks[1:]:
                if (t["x"], t["y"]) not in reach:
                    P.append(f"{t['id']} isolado (sem caminho até {toks[0]['id']})")
    if cdir:
        P += _ref_problems(m, cdir)
    return P


def _ref_problems(m, cdir):
    import world
    ctx = world.Ctx(cdir)
    mids, _ = campaign_ids(cdir)
    mids.add(m["id"])
    P = []
    for e in _elems(m):
        for key in ("ref", "faction"):
            v = e.get(key)
            if v and not ctx.exists(v, mids):
                P.append(f"{e['id']}: {key} '{v}' não existe na campanha")
        if e.get("link_map") and e["link_map"] not in mids:
            P.append(f"{e['id']}: link_map '{e['link_map']}' não existe")
    for c in m["connections"]:
        if c["to"] not in mids:
            P.append(f"conexão para mapa inexistente: {c['to']}")
    return P


def verify_maps(cdir, ctx):
    """Usado pelo verificador de continuidade (somente leitura)."""
    P, by_id = [], {}
    maps = load_all(cdir)
    for m in maps:
        for e in validate(m, cdir):
            P.append(f"[mapa {m['id']}] {e}")
        for pt in m["points"]:
            prev = by_id.get(pt["id"])
            if prev and (slug(prev[1]["name"]) != slug(pt["name"]) or prev[1].get("ref") != pt.get("ref")):
                P.append(f"[mapa] {pt['id']} difere entre {prev[0]} e {m['id']} (nome/ref)")
            by_id.setdefault(pt["id"], (m["id"], pt))
    refs = {}
    for m in maps:
        for pt in m["points"]:
            if pt.get("ref"):
                refs.setdefault(pt["ref"], set()).add(pt["id"])
    for ref, pids in sorted(refs.items()):
        if len(pids) > 1:
            P.append(f"[duplicado] {ref} aparece como locais diferentes: {', '.join(sorted(pids))}")
    locs = {slug(e["meta"].get("name", "")): k for k, e in ctx.ent.items() if k.startswith("loc-")}
    for m in maps:
        for pt in m["points"]:
            if not pt.get("ref") and slug(pt["name"]) in locs:
                P.append(f"[duplicado?] {pt['id']} ({pt['name']}) tem o mesmo nome de {locs[slug(pt['name'])]}; vincule com 'map set {m['id']} {pt['id']} ref={locs[slug(pt['name'])]}'")
    # mapa do jogador sem segredos
    pub = mdir(cdir) / "jogador"
    if pub.exists():
        leak = _secret_strings(maps, ctx)
        for f in sorted(pub.iterdir()):
            txt = f.read_text(encoding="utf-8", errors="ignore")
            for s in leak:
                if s in txt:
                    P.append(f"[segredo exposto] maps/jogador/{f.name} contém '{s[:50]}'")
                    break
    return P


# ================================================================= visão do jogador
def _status(m, ctx, e):
    st = m["discovery"].get(e["id"])
    if not st and e.get("ref") and ctx.is_public(e["ref"]):
        st = "conhecido"  # mesma autoridade: '## Conhecido pelos jogadores' do arquivo do local
    return st


def _secret_strings(maps, ctx):
    """Textos que NUNCA podem aparecer na saída pública (a não ser que revelados)."""
    out = set()
    for m in maps:
        pv = player_view(m, ctx)
        shown = set()
        if pv:
            shown = {e["id"] for e in _elems(pv)}
        shown_names = {x["name"] for x in _elems(pv)} if pv else set()
        for e in _elems(m):
            if e.get("gm_note"):
                out.add(e["gm_note"])
            if e["id"] not in shown and e.get("secret"):
                out.add(e["id"])
                if len(e["name"]) > 6 and e["name"] not in shown_names:
                    out.add(e["name"])
        if not pv:
            out.add(m["id"])
    return sorted(s for s in out if s and len(s) > 5)


def player_view(m, ctx):
    """Cópia filtrada com SÓ o que os personagens conhecem. None se o mapa ainda não foi revelado."""
    disc = {e["id"]: _status(m, ctx, e) for e in _elems(m)}
    disc = {k: v for k, v in disc.items() if v}
    map_known = m["discovery"].get(m["id"])
    tok_ids = {t["id"] for t in m["layers"].get("tokens", [])}
    if not map_known and (m["type"] == "batalha" or not set(disc) - tok_ids):
        return None  # mapa só aparece aos jogadores depois de revelado (batalha: quando o mestre abrir o combate)
    W, H = m["size"]["w"], m["size"]["h"]
    lay = m["layers"]
    out = dict(id=m["id"], campaign=m["campaign"], type=m["type"], name=m["name"],
               description=m.get("public_description", ""), size=m["size"], connections=[], discovery={}, layers={})

    def pub_pt(e, st):
        q = {k: e[k] for k in ("id", "name", "kind", "x", "y", "level", "port", "area", "entrance", "radius") if k in e}
        q["status"] = st
        if e.get("note"):
            q["note"] = e["note"]
        if e.get("ref") and ctx.is_public(e["ref"]):
            q["ref"] = e["ref"]
        if e.get("link_map"):
            q["link_map"] = e["link_map"]
        return q

    if m["type"] in ("mundo", "regiao"):
        rows = lay["regions"]
        known_a = {a["id"] for a in m["areas"] if disc.get(a["id"]) in ("conhecido", "visitado")
                   and (not a.get("secret") or a["id"] in m["discovery"])}
        if map_known == "visitado":  # mapa inteiro revelado (exceto o secreto)
            known_a |= {a["id"] for a in m["areas"] if not a.get("secret")}
        chars = {AC[i] for i, a in enumerate(m["areas"]) if a["id"] in known_a}
        vis = {(x, y) for y in range(H) for x in range(W) if rows[y][x] in chars}
        if map_known == "visitado":
            vis |= {(x, y) for y in range(H) for x in range(W) if lay["biomes"][y][x] in WATER}
        else:
            vis |= {(x + dx, y + dy) for (x, y) in vis for dx in (-2, -1, 0, 1, 2) for dy in (-2, -1, 0, 1, 2)
                    if 0 <= x + dx < W and 0 <= y + dy < H and lay["biomes"][y + dy][x + dx] in WATER}
        out["layers"]["biomes"] = ["".join(lay["biomes"][y][x] if (x, y) in vis else "?" for x in range(W)) for y in range(H)]
        keep_areas = [a for a in m["areas"] if a["id"] in known_a or disc.get(a["id"]) == "rumor" and not a.get("secret")]
        remap = {AC[m["areas"].index(a)]: AC[i] for i, a in enumerate(keep_areas) if a["id"] in known_a}
        out["layers"]["regions"] = ["".join(remap.get(rows[y][x], ".") if (x, y) in vis else "." for x in range(W)) for y in range(H)]

        def clip(paths):
            res = []
            for path in paths:
                seg = []
                for c in path["pts"] if isinstance(path, dict) else path:
                    if tuple(c) in vis:
                        seg.append(c)
                    else:
                        if len(seg) > 1:
                            res.append(dict(path, pts=seg) if isinstance(path, dict) else seg)
                        seg = []
                if len(seg) > 1:
                    res.append(dict(path, pts=seg) if isinstance(path, dict) else seg)
            return res
        out["layers"]["rivers"] = clip(lay["rivers"])
        out["layers"]["roads"] = clip(lay["roads"])
        out["areas"] = [dict(id=a["id"], name=a["name"], kind=a["kind"], status=disc.get(a["id"]) or map_known,
                             **({"note": a["note"]} if a.get("note") else {})) for a in keep_areas]
    elif m["type"] == "cidade":
        cells, dist = lay["cells"], lay["districts"]
        known_a = {a["id"] for a in m["areas"] if disc.get(a["id"]) in ("conhecido", "visitado") and not a.get("secret")}
        if map_known == "visitado":
            known_a |= {a["id"] for a in m["areas"] if not a.get("secret")}
        chars = {AC[i] for i, a in enumerate(m["areas"]) if a["id"] in known_a}
        outline = set("WGR~B") if map_known else set()
        out["layers"]["cells"] = ["".join(cells[y][x] if dist[y][x] in chars or cells[y][x] in outline or
                                          (map_known and cells[y][x] == " ") else "?" for x in range(W)) for y in range(H)]
        keep = [a for a in m["areas"] if a["id"] in known_a or disc.get(a["id"]) == "rumor"]
        remap = {AC[m["areas"].index(a)]: AC[i] for i, a in enumerate(keep) if a["id"] in known_a}
        out["layers"]["districts"] = ["".join(remap.get(dist[y][x], ".") for x in range(W)) for y in range(H)]
        out["areas"] = [dict(id=a["id"], name=a["name"], kind=a["kind"], status=disc.get(a["id"]) or map_known) for a in keep]
        if map_known:
            for e in m["points"]:
                if e["kind"] in ("portao", "saida") and not e.get("secret"):
                    disc.setdefault(e["id"], "conhecido")
    elif m["type"] == "masmorra":
        out["layers"]["levels"] = []
        out["areas"] = []
        for i, lv in enumerate(lay["levels"]):
            lareas = [a for a in m["areas"] if a.get("level") == i + 1]
            known = {AC[j] for j, a in enumerate(lareas) if (disc.get(a["id"]) in ("conhecido", "visitado") or map_known == "visitado" and not a.get("secret"))
                     and (not a.get("secret") or a["id"] in m["discovery"])}
            g, A = lv["grid"], lv["areas"]
            floor = {(x, y) for y in range(H) for x in range(W) if A[y][x] in known}
            found_secret = {(e["x"], e["y"]) for e in m["points"] if e["kind"] == "porta_secreta" and e.get("level") == i + 1 and disc.get(e["id"])}
            rows = []
            for y in range(H):
                row = []
                for x in range(W):
                    ch = g[y][x]
                    near = any((x + dx, y + dy) in floor for dx, dy in N8) or (x, y) in floor
                    if (x, y) in floor:
                        row.append(ch)
                    elif ch in "+<>" and near:
                        row.append(ch)
                    elif ch == "S" and near and (x, y) in found_secret:
                        row.append("S")
                    elif ch in "#S" and near:
                        row.append("#")
                    else:
                        row.append("?")
                rows.append("".join(row))
            ka = [a for a in lareas if AC[lareas.index(a)] in known]
            remap = {AC[lareas.index(a)]: AC[j] for j, a in enumerate(ka)}
            out["layers"]["levels"].append({"grid": rows, "areas": ["".join(remap.get(c, ".") for c in r) for r in A]})
            out["areas"] += [dict(id=a["id"], name=a["name"], kind=a["kind"], level=a["level"], status=disc.get(a["id"]) or map_known) for a in ka]
            for e in m["points"]:  # pontos não secretos em áreas reveladas são visíveis
                if e.get("level") == i + 1 and not e.get("secret") and (e["x"], e["y"]) in floor:
                    disc.setdefault(e["id"], "conhecido")
    else:  # batalha
        g = lay["grid"]
        found = {(e["x"], e["y"]) for e in m["points"] if e.get("kind") == "porta_secreta" and disc.get(e["id"])}
        out["layers"]["grid"] = ["".join("#" if c == "S" and (x, y) not in found else c for x, c in enumerate(r)) for y, r in enumerate(g)]
        out["layers"]["tokens"] = [{k: t[k] for k in ("id", "name", "side", "x", "y", "ref") if k in t}
                                   for t in lay.get("tokens", []) if not t.get("secret") or disc.get(t["id"])]
        out["areas"] = []
        for e in m["points"]:
            if not e.get("secret"):
                disc.setdefault(e["id"], "conhecido")
    out["points"] = [pub_pt(e, disc[e["id"]]) for e in m["points"]
                     if disc.get(e["id"]) and (not e.get("secret") or e["id"] in m["discovery"])]
    out["discovery"] = {k: v for k, v in disc.items() if any(e["id"] == k for e in out["points"] + out["areas"])}
    out["connections"] = [c for c in m["connections"] if m["discovery"].get(c["to"]) or c.get("public")]
    return out


# ================================================================= SVG
def esc(s):
    return html.escape(str(s), quote=True)


def _runs(rows, cs, colors, ox=0, oy=0, skip=""):
    by = {}
    for y, row in enumerate(rows):
        x = 0
        while x < len(row):
            ch = row[x]
            k = x
            while k < len(row) and row[k] == ch:
                k += 1
            if ch not in skip and ch in colors:
                by.setdefault(colors[ch], []).append(f'<rect x="{ox + x * cs}" y="{oy + y * cs}" width="{(k - x) * cs}" height="{cs}"/>')
            x = k
    return "".join(f'<g fill="{c}">{"".join(r)}</g>' for c, r in sorted(by.items()))


def _edges(rows, cs, differ, ox=0, oy=0):
    segs = []
    H, W = len(rows), len(rows[0]) if rows else 0
    for y in range(H):
        for x in range(W):
            if x + 1 < W and differ(rows[y][x], rows[y][x + 1]):
                segs.append(f"M{ox + (x + 1) * cs} {oy + y * cs}v{cs}")
            if y + 1 < H and differ(rows[y][x], rows[y + 1][x]):
                segs.append(f"M{ox + x * cs} {oy + (y + 1) * cs}h{cs}")
    return "".join(segs)


def _label(x, y, text, size=9, weight="normal", style="normal", fill="#222", anchor="middle"):
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" font-weight="{weight}" font-style="{style}" '
            f'text-anchor="{anchor}" fill="{fill}" stroke="#fffdf5" stroke-width="2.5" paint-order="stroke">{esc(text)}</text>')


ICON = {
    "capital": lambda x, y: f'<path d="M{x} {y - 6}l1.8 4h4.2l-3.4 2.6 1.3 4.2-3.9-2.6-3.9 2.6 1.3-4.2-3.4-2.6h4.2z" fill="#b22" stroke="#400" stroke-width=".6"/>',
    "cidade": lambda x, y: f'<rect x="{x - 3.5}" y="{y - 3.5}" width="7" height="7" fill="#c33" stroke="#400" stroke-width=".8"/>',
    "vila": lambda x, y: f'<circle cx="{x}" cy="{y}" r="2.6" fill="#fff" stroke="#400" stroke-width="1"/>',
    "fortaleza": lambda x, y: f'<path d="M{x - 4} {y + 3}v-6h2v2h1.5v-2h1v2h1.5v-2h2v6z" fill="#555" stroke="#111" stroke-width=".6"/>',
    "ruina": lambda x, y: f'<path d="M{x - 3} {y - 3}l6 6M{x + 3} {y - 3}l-6 6" stroke="#5a3d1e" stroke-width="1.6"/>',
    "ponte": lambda x, y: f'<path d="M{x - 3} {y - 1.5}h6M{x - 3} {y + 1.5}h6" stroke="#6b4a2b" stroke-width="1.3"/>',
    "passagem": lambda x, y: f'<path d="M{x - 4} {y + 2}l4-5 4 5" fill="none" stroke="#333" stroke-width="1.4"/>',
}
DEFAULT_ICON = lambda x, y: f'<path d="M{x} {y - 4}l4 4-4 4-4-4z" fill="#2a6" stroke="#033" stroke-width=".8"/>'
GRID_ICON = {"entrada": ("▲", "#060"), "escada_sobe": ("▲", "#060"), "escada_desce": ("▼", "#060"), "armadilha": ("⚠", "#c00"),
             "tesouro": ("$", "#b8860b"), "encontro": ("☠", "#900"), "porta_secreta": ("S", "#80c"), "ponto": ("◆", "#036"),
             "aoe": ("✺", "#d60")}
CITY_ICON = {"mercado": "⚖", "taverna": "🍺", "templo": "✝", "guilda": "⚒", "palacio": "♛", "quartel": "⚔",
             "estalagem": "⌂", "portao": "▣", "saida": "➜", "porto": "⚓"}


def _point_svg(p, x, y, view, icon_svg):
    st = p.get("status")
    cls = "pt" + (" secreto" if p.get("secret") else "") + (f" {st}" if st else "")
    op = ' opacity=".55"' if st == "rumor" else ""
    sec = f'<circle cx="{x}" cy="{y}" r="7" fill="none" stroke="#80c" stroke-dasharray="2 1.5" stroke-width="1.2"/>' if view == "mestre" and p.get("secret") else ""
    name = p["name"] + (" (?)" if st == "rumor" else "")
    lab = _label(x, y + 13, name, 9, "bold" if st == "visitado" or p.get("kind") == "capital" else "normal",
                 "italic" if st == "rumor" else "normal", "#5a1a7a" if sec else "#222")
    return f'<g class="{cls}" data-id="{esc(p["id"])}"{op}><title>{esc(name)}</title>{sec}{icon_svg}{lab}</g>'


def _frame(w, h, body, title, legend, sub=""):
    LW = 190
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w + LW} {h}" width="{w + LW}" height="{h}" '
            f'font-family="Georgia, serif"><rect width="{w + LW}" height="{h}" fill="#f4ecd8"/>{body}'
            f'<g transform="translate({w + 10},10)"><text font-size="15" font-weight="bold" fill="#3a2a10">{esc(title)}</text>'
            f'<text y="16" font-size="10" fill="#6b5a3a">{esc(sub)}</text>{legend}</g></svg>')


def _compass(x, y):
    return (f'<g transform="translate({x},{y})" fill="#3a2a10"><path d="M0 -16l5 14h-10z"/><path d="M0 16l5-14h-10z" fill="#bba"/>'
            f'<text y="-19" font-size="10" text-anchor="middle">N</text></g>')


def _scalebar(x, y, cs, per_cell, unit):
    for n in (1, 2, 5, 10, 20, 25, 50, 100, 200, 500):
        if n / per_cell * cs >= 50:
            break
    px = n / per_cell * cs
    return (f'<g transform="translate({x},{y})"><rect width="{px:.1f}" height="4" fill="#3a2a10"/><rect width="{px / 2:.1f}" height="4" fill="#f4ecd8" stroke="#3a2a10" stroke-width=".8"/>'
            f'<text x="{px + 6:.1f}" y="5" font-size="9">{n} {unit} (escala ilustrativa)</text></g>')


def _legend(items, y0=40):
    out, y = [], y0
    for sw, label in items:
        out.append(f'<g transform="translate(0,{y})">{sw}<text x="20" y="4" font-size="10">{esc(label)}</text></g>')
        y += 16
    return "".join(out), y


def svg_world(m, view):
    lay = m["layers"]
    W, H = m["size"]["w"], m["size"]["h"]
    cs = 6 if W <= 180 else 4
    w, h = W * cs, H * cs
    B = lay["biomes"]
    body = [_runs(B, cs, {k: v[1] for k, v in BIOME.items()})]
    deco = []
    for y in range(H):
        for x in range(W):
            ch = B[y][x]
            px, py = x * cs + cs / 2, y * cs + cs / 2
            if ch in "m*" and (x + 2 * y) % 3 == 0 and x % 2 == 0:
                deco.append(f"M{px - 3.5} {py + 2.5}l3.5-6 3.5 6")
            elif ch == "h" and (x * 3 + y) % 6 == 0:
                deco.append(f"M{px - 3} {py + 1.5}q3-4 6 0")
    body.append(f'<path d="{"".join(deco)}" fill="none" stroke="#4d4236" stroke-width=".8"/>')
    trees = [f'<circle cx="{x * cs + cs / 2}" cy="{y * cs + cs / 2}" r="1.6"/>' for y in range(H) for x in range(W)
             if B[y][x] == "f" and (x * 7 + y * 13) % 5 == 0]
    body.append(f'<g fill="#3d6b33">{"".join(trees)}</g>')
    body.append(f'<path d="{_edges(B, cs, lambda a, b: (a in WATER) != (b in WATER) and "?" not in (a + b))}" stroke="#2b3d4f" stroke-width="1.1" fill="none"/>')
    R = lay["regions"]
    body.append(f'<path d="{_edges(R, cs, lambda a, b: a != b and "." not in (a + b))}" stroke="#7a1f3d" stroke-width="1.4" stroke-dasharray="4 2" fill="none" opacity=".8"/>')
    for r in lay["rivers"]:
        pts = " ".join(f"{(c[0] + .5) * cs:.1f},{(c[1] + .5) * cs:.1f}" for c in r["pts"])
        body.append(f'<polyline points="{pts}" fill="none" stroke="#3f7fbf" stroke-width="{0.7 + r["w"] * .6}" stroke-linejoin="round"/>')
    for road in lay["roads"]:
        pts = " ".join(f"{(c[0] + .5) * cs:.1f},{(c[1] + .5) * cs:.1f}" for c in road)
        body.append(f'<polyline points="{pts}" fill="none" stroke="#7a5230" stroke-width="1.1" stroke-dasharray="3 2"/>')
    for i, a in enumerate(m["areas"]):
        cells = [(x, y) for y in range(H) for x in range(W) if R[y][x] == AC[i]]
        if not cells:
            continue
        mx = sum(c[0] for c in cells) / len(cells) * cs
        my = sum(c[1] for c in cells) / len(cells) * cs
        sec = view == "mestre" and a.get("secret")
        body.append(f'<g class="area" data-id="{esc(a["id"])}"><title>{esc(a["name"])}</title>'
                    f'{_label(mx, my, a["name"].upper(), 13, "bold", "normal", "#5a1a7a" if sec else "#5b2a1a")}</g>')
    for p in m["points"]:
        x, y = p["x"] * cs + cs / 2, p["y"] * cs + cs / 2
        icon = ICON.get(p["kind"], DEFAULT_ICON)(x, y)
        if p.get("port"):
            icon += f'<text x="{x + 5}" y="{y - 3}" font-size="8" fill="#123">⚓</text>'
        body.append(_point_svg(p, x, y, view, icon))
    body.append(_compass(w - 26, 30))
    body.append(_scalebar(12, h - 22, cs, m["size"]["per_cell"], m["size"]["unit"]))
    present = sorted({c for r in B for c in r}, key=lambda c: list(BIOME).index(c))
    items = [(f'<rect x="0" y="-5" width="14" height="10" fill="{BIOME[c][1]}" stroke="#776"/>', BIOME[c][0]) for c in present]
    items += [(ICON[k](7, 0), lab) for k, lab in (("capital", "Capital"), ("cidade", "Cidade"), ("vila", "Vila"), ("fortaleza", "Fortaleza"),
                                                  ("ruina", "Ruína"), ("ponte", "Ponte"), ("passagem", "Passagem"))]
    items += [('<path d="M0 0h14" stroke="#7a5230" stroke-dasharray="3 2" stroke-width="1.2"/>', "Estrada"),
              ('<path d="M0 0h14" stroke="#3f7fbf" stroke-width="1.6"/>', "Rio"),
              ('<path d="M0 0h14" stroke="#7a1f3d" stroke-dasharray="4 2" stroke-width="1.4"/>', "Fronteira")]
    if view == "mestre":
        items.append(('<circle cx="7" cy="0" r="6" fill="none" stroke="#80c" stroke-dasharray="2 1.5"/>', "Secreto (só mestre)"))
    else:
        items.append(('<text x="2" y="4" font-size="9" font-style="italic" opacity=".6">X (?)</text>', "Rumor"))
    leg, _ = _legend(items)
    sub = f"{'MESTRE' if view == 'mestre' else 'JOGADOR'} · {m['type']}" + (f" · seed {m['generator']['seed']}" if view == "mestre" else "")
    return _frame(w, h, "".join(body), m["name"], leg, sub)


CITY_COL = {" ": "#d9dfb5", "b": "#b08a6a", "r": "#ece5d2", "R": "#dcc9a0", "p": "#eadcb5", "W": "#4b4b4b",
            "G": "#8b5a2b", "~": "#5b9bd0", "B": "#8b5a2b", "?": "#e9dfc6"}
TINTS = ["#d8b4a0", "#c9c08f", "#a9b8c9", "#c8a9c4", "#b5c9a6", "#d6c39a", "#a8c4bf", "#cfa8a8", "#bdb0d6", "#c4c4c4"]


def svg_city(m, view):
    lay = m["layers"]
    W, H = m["size"]["w"], m["size"]["h"]
    cs = 8
    w, h = W * cs, H * cs
    C, D = lay["cells"], lay["districts"]
    tint_rows = ["".join(D[y][x] if C[y][x] == "b" else "_" for x in range(W)) for y in range(H)]
    body = [_runs(C, cs, CITY_COL),
            _runs(tint_rows, cs, {AC[i]: TINTS[i % len(TINTS)] for i in range(len(m["areas"]))}, skip="_")]
    blds = []
    for y in range(0, H - 1, 2):
        for x in range(0, W - 1, 2):
            if C[y][x] == C[y + 1][x] == C[y][x + 1] == C[y + 1][x + 1] == "b":
                blds.append(f'<rect x="{x * cs + 1.5}" y="{y * cs + 1.5}" width="{2 * cs - 3}" height="{2 * cs - 3}"/>')
    body.append(f'<g fill="none" stroke="#6b4e36" stroke-width=".7" opacity=".7">{"".join(blds)}</g>')
    body.append(f'<path d="{_edges(D, cs, lambda a, b: a != b and "." not in (a + b))}" stroke="#7a1f3d" stroke-width="1" stroke-dasharray="3 3" fill="none" opacity=".5"/>')
    for i, a in enumerate(m["areas"]):
        cells = [(x, y) for y in range(H) for x in range(W) if D[y][x] == AC[i]]
        if not cells:
            continue
        mx = sum(c[0] for c in cells) / len(cells) * cs
        my = sum(c[1] for c in cells) / len(cells) * cs
        body.append(f'<g class="area" data-id="{esc(a["id"])}"><title>{esc(a["name"])}</title>{_label(mx, my, a["name"].upper(), 11, "bold", "normal", "#5b2a1a")}</g>')
    for p in m["points"]:
        x, y = p["x"] * cs + cs / 2, p["y"] * cs + cs / 2
        sym = CITY_ICON.get(p["kind"], "◆")
        icon = f'<circle cx="{x}" cy="{y}" r="5.5" fill="#fffdf5" stroke="#3a2a10"/><text x="{x}" y="{y + 3.2}" font-size="8" text-anchor="middle">{sym}</text>'
        body.append(_point_svg(p, x, y, view, icon))
    body.append(_compass(w - 26, 30))
    body.append(_scalebar(12, h - 22, cs, m["size"]["per_cell"], m["size"]["unit"]))
    items = [(f'<rect x="0" y="-5" width="14" height="10" fill="{CITY_COL[k]}" stroke="#776"/>', lab) for k, lab in
             (("b", "Construções"), ("r", "Rua"), ("R", "Via principal"), ("p", "Praça"), ("W", "Muralha"), ("G", "Portão"), ("~", "Rio"), ("B", "Ponte"), (" ", "Campo"))]
    items += [(f'<circle cx="7" cy="0" r="5.5" fill="#fffdf5" stroke="#3a2a10"/><text x="7" y="3" font-size="8" text-anchor="middle">{s}</text>', k.capitalize())
              for k, s in CITY_ICON.items()]
    leg, _ = _legend(items)
    sub = f"{'MESTRE' if view == 'mestre' else 'JOGADOR'} · cidade" + (f" · seed {m['generator']['seed']}" if view == "mestre" else "")
    return _frame(w, h, "".join(body), m["name"], leg, sub)


GRID_COL = {"#": "#3b3631", ".": "#efe8d8", "+": "#a0673a", "S": "#7b4fa0", "<": "#cde3c4", ">": "#cde3c4",
            "c": "#b9935f", ":": "#cbbf98", "~": "#7fb2d9", "?": "#2b2724"}


def svg_grid(m, view, level=None):
    lay = m["layers"]
    W, H = m["size"]["w"], m["size"]["h"]
    cs = 20 if m["type"] == "masmorra" else 28
    ox = oy = 18
    w, h = W * cs + ox, H * cs + oy
    rows = lay["levels"][level - 1]["grid"] if level else lay["grid"]
    body = [_runs(rows, cs, GRID_COL, ox, oy)]
    gl = [f"M{ox + x * cs} {oy}v{H * cs}" for x in range(W + 1)] + [f"M{ox} {oy + y * cs}h{W * cs}" for y in range(H + 1)]
    body.append(f'<path d="{"".join(gl)}" stroke="#8a7f6a" stroke-width=".4" opacity=".6"/>')
    hatch = [f"M{ox + x * cs + 3} {oy + y * cs + cs - 3}l{cs - 6} {-(cs - 6)}" for y in range(H) for x in range(W) if rows[y][x] == ":"]
    body.append(f'<path d="{"".join(hatch)}" stroke="#8a7a50" stroke-width=".8"/>')
    for y in range(H):
        for x in range(W):
            ch = rows[y][x]
            if ch in "<>":
                body.append(_label(ox + x * cs + cs / 2, oy + y * cs + cs * .7, "▲" if ch == "<" else "▼", cs * .55, fill="#060"))
            elif ch == "S" and view == "mestre":
                body.append(_label(ox + x * cs + cs / 2, oy + y * cs + cs * .7, "S", cs * .5, "bold", fill="#fff"))
    for x in range(W):
        body.append(f'<text x="{ox + x * cs + cs / 2}" y="12" font-size="9" text-anchor="middle" fill="#555">{_letters(x)}</text>')
    for y in range(H):
        body.append(f'<text x="9" y="{oy + y * cs + cs * .65}" font-size="9" text-anchor="middle" fill="#555">{y + 1}</text>')
    if level:
        lareas = [a for a in m["areas"] if a.get("level") == level]
        A = lay["levels"][level - 1]["areas"]
        for i, a in enumerate(lareas):
            if a["kind"] != "sala":
                continue
            cells = [(x, y) for y in range(H) for x in range(W) if A[y][x] == AC[i]]
            if cells:
                mx = ox + sum(c[0] for c in cells) / len(cells) * cs + cs / 2
                my = oy + sum(c[1] for c in cells) / len(cells) * cs + cs / 2
                body.append(f'<g class="area" data-id="{esc(a["id"])}"><title>{esc(a["name"])}</title>'
                            f'<text x="{mx}" y="{my + 4}" font-size="12" font-weight="bold" text-anchor="middle" fill="#7a6a50" opacity=".8">{esc(a["name"].split(" ")[1])}</text></g>')
    for p in m["points"]:
        if level and p.get("level") != level:
            continue
        x, y = ox + p["x"] * cs + cs / 2, oy + p["y"] * cs + cs / 2
        if p["kind"] == "aoe":
            r = p.get("radius", 1) * cs
            body.append(f'<g class="pt" data-id="{esc(p["id"])}"><title>{esc(p["name"])}</title><circle cx="{x}" cy="{y}" r="{r}" fill="#f80" fill-opacity=".18" stroke="#d60" stroke-dasharray="4 2"/></g>')
            continue
        sym, col = GRID_ICON.get(p["kind"], ("◆", "#036"))
        icon = f'<text x="{x}" y="{y + cs * .22}" font-size="{cs * .6}" text-anchor="middle" fill="{col}" font-weight="bold">{sym}</text>'
        g = _point_svg(p, x, y, view, icon)
        body.append(g.replace(f'y="{y + 13:.1f}"', f'y="{y + cs * .95:.1f}"').replace('font-size="9"', 'font-size="8"', 1))
    for t in lay.get("tokens", []):
        x, y = ox + t["x"] * cs + cs / 2, oy + t["y"] * cs + cs / 2
        col = {"grupo": "#2b6cb0", "inimigo": "#b02b2b"}.get(t["side"], "#777")
        ini = "".join(w[0] for w in re.split(r"[\s-]+", t["name"].removeprefix("pc-")) if w)[:2].upper()
        sec = ' stroke-dasharray="3 2"' if t.get("secret") else ""
        body.append(f'<g class="pt tok" data-id="{esc(t["id"])}"><title>{esc(t["name"])} ({coord(t["x"], t["y"])})</title>'
                    f'<circle cx="{x}" cy="{y}" r="{cs * .4}" fill="{col}" stroke="#111" stroke-width="1.2"{sec}/>'
                    f'<text x="{x}" y="{y + 4}" font-size="10" text-anchor="middle" fill="#fff" font-weight="bold">{esc(ini)}</text></g>')
    items = [(f'<rect x="0" y="-5" width="14" height="10" fill="{GRID_COL[k]}" stroke="#776"/>', lab) for k, lab in
             (((".", "Chão"), ("#", "Parede/obstáculo"), ("+", "Porta"), (":", "Terreno difícil"), ("c", "Cobertura"), ("~", "Água"))
              + ((("S", "Porta secreta"),) if view == "mestre" else ()) + ((("?", "Inexplorado"),) if view == "jogador" else ()))
             if any(k in r for r in rows)]
    items += [(f'<text x="7" y="4" font-size="11" text-anchor="middle" fill="{c}">{s}</text>', k.replace("_", " ").capitalize())
              for k, (s, c) in GRID_ICON.items() if any(p["kind"] == k and (not level or p.get("level") == level) for p in m["points"])]
    if lay.get("tokens"):
        items += [('<circle cx="7" cy="0" r="6" fill="#2b6cb0"/>', "Grupo"), ('<circle cx="7" cy="0" r="6" fill="#b02b2b"/>', "Inimigos")]
    leg, y = _legend(items)
    leg += f'<text y="{y + 10}" font-size="9" fill="#555">1 quadrado = {m["size"]["per_cell"]:g} {m["size"]["unit"]}</text>'
    title = m["name"] + (f" — nível {level}" if level else "")
    sub = f"{'MESTRE' if view == 'mestre' else 'JOGADOR'} · {m['type']}" + (f" · seed {m['generator']['seed']}" if view == "mestre" else "")
    return _frame(w, h, "".join(body), title, leg, sub)


def render_svgs(m, view):
    """[(sufixo, svg)] — um por nível em masmorras."""
    if m["type"] in ("mundo", "regiao"):
        return [("", svg_world(m, view))]
    if m["type"] == "cidade":
        return [("", svg_city(m, view))]
    if m["type"] == "masmorra":  # jogador não vê (nem sabe da existência de) níveis inexplorados
        return [(f".L{i + 1}", svg_grid(m, view, i + 1)) for i, lv in enumerate(m["layers"]["levels"])
                if view == "mestre" or any(ch != "?" for r in lv["grid"] for ch in r)]
    return [("", svg_grid(m, view))]


# ================================================================= HTML (visualizador local, sem CDN)
VIEWER_JS = r"""
const D = JSON.parse(document.getElementById('dados').textContent);
const $ = s => document.querySelector(s);
let cur = null;
function lista(){ const ul=$('#mapas'); ul.innerHTML='';
  D.maps.forEach((m,i)=>m.levels.forEach((lv,j)=>{ const li=document.createElement('li');
    li.textContent = m.name + (m.levels.length>1?' — nível '+(j+1):''); li.title=m.type; li.onclick=()=>abrir(i,j);
    li.dataset.k=i+':'+j; ul.appendChild(li);})); }
function abrir(i,j){ cur={i,j}; const m=D.maps[i], lv=m.levels[j];
  document.querySelectorAll('#mapas li').forEach(li=>li.classList.toggle('on', li.dataset.k===i+':'+j));
  $('#palco').innerHTML = lv.svg; $('#svglink').href = lv.file; $('#svglink').textContent='Abrir SVG ('+lv.file+')';
  const s=$('#palco svg'); s.removeAttribute('width'); s.removeAttribute('height');
  const vb=s.getAttribute('viewBox').split(' ').map(Number); s._vb=vb.slice(); s._orig=vb.slice();
  s.addEventListener('click', ev=>{ const g=ev.target.closest('[data-id]'); if(g) info(g.dataset.id); });
  info(null); }
function setvb(s){ s.setAttribute('viewBox', s._vb.join(' ')); }
document.addEventListener('wheel', ev=>{ const s=$('#palco svg'); if(!s||!ev.target.closest('#palco')) return; ev.preventDefault();
  const r=s.getBoundingClientRect(), k=ev.deltaY>0?1.15:1/1.15, [x,y,w,h]=s._vb;
  const px=x+(ev.clientX-r.left)/r.width*w, py=y+(ev.clientY-r.top)/r.height*h;
  s._vb=[px-(px-x)*k, py-(py-y)*k, w*k, h*k]; setvb(s); }, {passive:false});
let drag=null;
document.addEventListener('mousedown', ev=>{ const s=$('#palco svg'); if(s&&ev.target.closest('#palco')) drag={x:ev.clientX,y:ev.clientY,vb:s._vb.slice()}; });
document.addEventListener('mousemove', ev=>{ if(!drag) return; const s=$('#palco svg'), r=s.getBoundingClientRect();
  s._vb=[drag.vb[0]-(ev.clientX-drag.x)/r.width*drag.vb[2], drag.vb[1]-(ev.clientY-drag.y)/r.height*drag.vb[3], drag.vb[2], drag.vb[3]]; setvb(s); });
document.addEventListener('mouseup', ()=>drag=null);
function zoom(k){ const s=$('#palco svg'); if(!s) return; const [x,y,w,h]=s._vb; s._vb=[x+w*(1-k)/2,y+h*(1-k)/2,w*k,h*k]; setvb(s); }
function reset(){ const s=$('#palco svg'); if(s){ s._vb=s._orig.slice(); setvb(s);} }
function el(t,txt,cls){ const e=document.createElement(t); if(txt!=null) e.textContent=txt; if(cls) e.className=cls; return e; }
function info(id){ const box=$('#info'); box.innerHTML='';
  const m=D.maps[cur.i];
  if(!id){ box.appendChild(el('h3',m.name)); if(m.description) box.appendChild(el('p',m.description));
    if(m.connections.length){ box.appendChild(el('h4','Mapas ligados')); m.connections.forEach(c=>box.appendChild(link(c.to, c.note))); }
    box.appendChild(el('p','Clique num local, região ou sala.','dica')); return; }
  const e=m.info[id]; if(!e){ box.appendChild(el('p','Sem informações.')); return; }
  box.appendChild(el('h3',e.name)); box.appendChild(el('div',[e.kind, e.status, e.coord].filter(Boolean).join(' · '),'meta'));
  (e.text||[]).forEach(t=>box.appendChild(el('p',t)));
  if(e.gm) e.gm.forEach(t=>box.appendChild(el('p',t,'gm')));
  if(e.link_map) box.appendChild(link(e.link_map,'Abrir mapa ligado'));
  if(e.level_link) { const b=el('button','Ir para o nível '+e.level_link); b.onclick=()=>abrir(cur.i,e.level_link-1); box.appendChild(b); }
  [['Relacionados',e.related],['Também neste mapa',e.nearby]].forEach(([t,xs])=>{ if(xs&&xs.length){ box.appendChild(el('h4',t));
    const ul=el('ul'); xs.forEach(x=>ul.appendChild(el('li',x))); box.appendChild(ul);} });
}
function link(mid, note){ const i=D.maps.findIndex(m=>m.id===mid); const b=el('button',(i>=0?D.maps[i].name:mid)+(note?' — '+note:''));
  b.disabled=i<0; b.onclick=()=>abrir(i,0); return b; }
lista(); if(D.maps.length) abrir(0,0); else $('#palco').textContent='Nenhum mapa disponível nesta visão.';
"""


def build_html(entries, view, campaign):
    data = json.dumps({"view": view, "maps": entries}, ensure_ascii=False).replace("</", "<\\/")
    gm = view == "mestre"
    return f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mapas — {esc(campaign)} ({'mestre' if gm else 'jogador'})</title>
<style>
:root{{--bg:#f4ecd8;--fg:#2b2111;--pan:#fbf6ea;--ac:{'#7a1f3d' if gm else '#2b6cb0'}}}
@media (prefers-color-scheme: dark){{:root{{--bg:#1d1a15;--fg:#eee4cf;--pan:#2a2620}}}}
body{{margin:0;font-family:Georgia,serif;background:var(--bg);color:var(--fg);display:grid;grid-template-columns:220px 1fr 300px;height:100vh}}
@media (max-width:900px){{body{{grid-template-columns:1fr;grid-template-rows:auto 60vh auto;height:auto}}}}
aside,#info{{background:var(--pan);overflow:auto;padding:12px}} h1{{font-size:15px;color:var(--ac);margin:0 0 8px}}
#mapas{{list-style:none;padding:0;margin:0}} #mapas li{{padding:6px;cursor:pointer;border-radius:4px}} #mapas li.on{{background:var(--ac);color:#fff}}
main{{position:relative;overflow:hidden;background:#d8cdb2}} #palco{{width:100%;height:100%}} #palco svg{{width:100%;height:100%;cursor:grab}}
.ctl{{position:absolute;top:8px;left:8px;display:flex;gap:4px}} button{{font:inherit;cursor:pointer;margin:2px 0}}
[data-id]{{cursor:pointer}} [data-id]:hover text{{fill:var(--ac)}} .meta{{font-size:12px;opacity:.75}} .gm{{border-left:3px solid #80c;padding-left:6px;font-style:italic}}
.dica{{opacity:.6;font-size:13px}} a{{color:var(--ac);font-size:12px;word-break:break-all}} .tag{{background:var(--ac);color:#fff;padding:2px 6px;border-radius:3px;font-size:11px}}
</style></head><body>
<aside><h1>{esc(campaign)}</h1><span class="tag">{'MAPA DO MESTRE' if gm else 'MAPA DOS JOGADORES'}</span><ul id="mapas"></ul><p><a id="svglink" target="_blank"></a></p></aside>
<main><div class="ctl"><button onclick="zoom(1/1.4)">+</button><button onclick="zoom(1.4)">−</button><button onclick="reset()">⟲</button></div><div id="palco"></div></main>
<section id="info"></section>
<script type="application/json" id="dados">{data}</script>
<script>{VIEWER_JS}</script></body></html>"""


def _info(m, ctx, view, cdir):
    """Painel de detalhes por elemento: descrição + relações (NPCs, missões, eventos)."""
    import world
    gm = view == "mestre"
    out = {}
    els = m.get("points", []) + m.get("areas", []) + m.get("layers", {}).get("tokens", [])
    for e in els:
        rec = {"name": e["name"], "kind": e.get("kind") or e.get("side"), "status": e.get("status") or m.get("discovery", {}).get(e["id"])}
        if "x" in e:
            rec["coord"] = coord(e["x"], e["y"]) if m["type"] in ("masmorra", "batalha", "cidade") else f"({e['x']}, {e['y']})"
        text = [e["note"]] if e.get("note") else []
        ref = e.get("ref")
        if ref and ref in ctx.ent:
            ent = ctx.ent[ref]
            if gm:
                desc = world.section(ent["text"], "Descrição")
                text += [x for x in (desc, ent["known"] and "Os jogadores sabem: " + ent["known"]) if x]
            elif ent["known"]:
                text.append(ent["known"])
        rec["text"] = text
        if gm:
            rec["gm"] = [x for x in (e.get("gm_note"), "SECRETO" if e.get("secret") else "", f"ref: {ref}" if ref else "") if x]
        if e.get("link_map"):
            rec["link_map"] = e["link_map"]
        if e.get("link"):
            rec["level_link"] = e["link"]["level"]
        related = []
        for key in [k for k in (ref, e["id"]) if k]:
            for r, other in world.rel_query(ctx, key, public=not gm):
                related.append(f"{ctx.name(other)} ({r['type'].replace('_', ' ')}{', segredo' if r.get('secret') else ''})")
            for ev in ctx.events:
                if (ev.get("loc") == key or key in ev.get("entities", [])) and (gm or world.is_public_event(ev)):
                    related.append(f"Evento {ev['id']} [{ev.get('date', '?')}]: {ev['text']}")
        rec["related"] = sorted(set(related))
        if e.get("area") or e.get("region"):
            par = e.get("area") or e.get("region")
            rec["nearby"] = [x["name"] for x in m["points"] if x is not e and (x.get("area") or x.get("region")) == par][:15]
        out[e["id"]] = rec
    return out


def render_campaign(cdir, png=False):
    import world
    ctx = world.Ctx(cdir)
    maps = load_all(cdir)
    base = mdir(cdir)
    written = {"mestre": [], "jogador": [], "png": []}
    pvs = {m["id"]: player_view(m, ctx) for m in maps}
    visible = {k for k, v in pvs.items() if v}
    for pv in filter(None, pvs.values()):  # ligação para mapa não revelado não pode vazar o id dele
        pv["connections"] = [c for c in pv["connections"] if c["to"] in visible]
        for pt in pv["points"]:
            if pt.get("link_map") not in visible:
                pt.pop("link_map", None)
    for view in ("mestre", "jogador"):
        out = base / view
        if out.exists():
            shutil.rmtree(out)  # derivado: recriado inteiro (sobra de mapa antes revelado não pode ficar)
        out.mkdir(parents=True)
        entries = []
        for m in maps:
            mv = m if view == "mestre" else pvs[m["id"]]
            if mv is None:
                continue
            levels = []
            for suf, svg in render_svgs(mv, view):
                f = out / f"{m['id']}{suf}.svg"
                f.write_text(svg, encoding="utf-8")
                written[view].append(f)
                levels.append({"file": f.name, "svg": svg})
            conns = mv["connections"] if view == "mestre" else [c for c in mv["connections"]]
            entries.append({"id": m["id"], "name": mv["name"], "type": m["type"],
                            "description": m.get("description", "") if view == "mestre" else mv.get("description", ""),
                            "connections": conns, "levels": levels, "info": _info(mv, ctx, view, cdir)})
        idx = out / "index.html"
        idx.write_text(build_html(entries, view, Path(cdir).name), encoding="utf-8")
        written[view].append(idx)
    if png:
        written["png"] = export_png(written["mestre"] + written["jogador"])
    return written


def export_png(svgs):
    """PNG opcional: usa rsvg-convert, inkscape ou Chrome/Chromium headless se instalados. Nada é instalado."""
    tool = next((t for t in ("rsvg-convert", "inkscape", "google-chrome", "chromium", "chromium-browser") if shutil.which(t)), None)
    if not tool:
        return []
    out = []
    for s in [s for s in svgs if s.suffix == ".svg"]:
        png = s.with_suffix(".png")
        m = re.search(r'width="(\d+)" height="(\d+)"', s.read_text(encoding="utf-8")[:400])
        w, h = (int(m.group(1)), int(m.group(2))) if m else (1200, 800)
        if tool == "rsvg-convert":
            cmd = [tool, "-o", str(png), str(s)]
        elif tool == "inkscape":
            cmd = [tool, str(s), "--export-type=png", f"--export-filename={png}"]
        else:
            prof = tempfile.mkdtemp(prefix="rpg-chrome-")
            cmd = [tool, "--headless", "--disable-gpu", "--no-first-run", "--no-default-browser-check", "--disable-extensions",
                   f"--user-data-dir={prof}", "--hide-scrollbars", f"--window-size={w},{h}", f"--screenshot={png}", s.resolve().as_uri()]
        try:
            subprocess.run(cmd, capture_output=True, timeout=60)
        except (subprocess.TimeoutExpired, OSError):
            pass
        finally:
            if tool not in ("rsvg-convert", "inkscape"):
                shutil.rmtree(prof, ignore_errors=True)
        if png.exists() and png.stat().st_size > 0:
            out.append(png)
    return out


# ================================================================= CLI
def _cdir(a):
    from rpg import camp_dir
    return camp_dir(a.campaign)


def _parse_kv(items):
    out = {}
    for it in items:
        if "=" not in it:
            raise MapError(f"use chave=valor, não '{it}'")
        k, v = it.split("=", 1)
        out[k] = {"true": True, "false": False, "": None}.get(v.lower(), v)
    return out


def cmd_gen(a):
    cdir = _cdir(a)
    mid = a.id if a.id.startswith("map-") else "map-" + slug(a.id)
    if (mdir(cdir) / f"{mid}.json").exists():
        raise MapError(f"mapa '{mid}' já existe — não sobrescrevo (use outro id ou 'map place' para expandir)")
    seed = a.seed if a.seed is not None else random.SystemRandom().randint(1, 2 ** 31 - 1)
    used_ids, used_names = _used(cdir)
    used_ids.add(mid)
    ms = mid.removeprefix("map-")
    t = a.kind
    over = {k: v for k, v in vars(a).items() if v is not None}
    parent = None
    if t in ("mundo", "regiao"):
        p = {k: over.get(k, v) for k, v in WORLD_DEF.items()}
        unit, per = "km", float(p["km"])
        if t == "regiao":
            if not a.parent:
                raise MapError("região precisa de --parent <mapa-mundo> e --area <reg-id> ou --bbox x0,y0,x1,y1")
            parent = load_map(cdir, a.parent)
            if parent["type"] not in ("mundo", "regiao"):
                raise MapError("--parent precisa ser um mapa de mundo/região")
            if a.area:
                i = next((i for i, ar in enumerate(parent["areas"]) if ar["id"] == a.area), None)
                if i is None:
                    raise MapError(f"área '{a.area}' não existe em {parent['id']}")
                cells = [(x, y) for y, r in enumerate(parent["layers"]["regions"]) for x, ch in enumerate(r) if ch == AC[i]]
                xs, ys = [c[0] for c in cells], [c[1] for c in cells]
                bb = [max(0, min(xs) - 2), max(0, min(ys) - 2), min(parent["size"]["w"], max(xs) + 3), min(parent["size"]["h"], max(ys) + 3)]
            elif a.bbox:
                bb = [int(v) for v in a.bbox.split(",")]
            else:
                raise MapError("informe --area ou --bbox")
            if bb[2] - bb[0] < 4 or bb[3] - bb[1] < 4:
                raise MapError("recorte pequeno demais")
            p["bbox"] = bb
            per = parent["size"]["per_cell"] * (bb[2] - bb[0]) / p["w"]
            seed = parent["generator"]["seed"]
        areas, points, layers = gen_world(seed, p, used_ids, used_names, parent)
        if t == "regiao":
            per = parent["size"]["per_cell"] * (p["bbox"][2] - p["bbox"][0]) / p["w"]
    elif t == "cidade":
        p = {k: over.get(k, v) for k, v in CITY_DEF.items()}
        if a.river is not None:
            p["river"] = a.river == "sim"
        unit, per = "m", float(p["m"])
        areas, points, layers = gen_city(seed, p, used_ids, used_names, ms)
    elif t == "masmorra":
        p = {k: over.get(k, v) for k, v in DUN_DEF.items()}
        unit, per = "m", float(p["m"])
        areas, points, layers = gen_dungeon(seed, p, used_ids, ms)
    else:
        p = {k: over.get(k, v) for k, v in BAT_DEF.items()}
        if p["env"] not in ENVS:
            raise MapError(f"--env deve ser um de: {', '.join(ENVS)}")
        unit, per = "m", float(p["m"])
        src_lv = src_area = None
        if a.source:
            src = load_map(cdir, a.source)
            if src["type"] != "masmorra" or not a.area:
                raise MapError("--from exige um mapa de masmorra e --area <id da sala>")
            ar = find_elem(src, a.area)
            if not ar or ar.get("kind") != "sala":
                raise MapError(f"sala '{a.area}' não existe em {src['id']}")
            lv = src["layers"]["levels"][ar["level"] - 1]
            mg = 3
            src_area = (max(0, ar["x"] - mg), max(0, ar["y"] - mg), min(src["size"]["w"], ar["x"] + ar["w"] + mg),
                        min(src["size"]["h"], ar["y"] + ar["h"] + mg))
            src_lv = lv
            p.update(source=a.source, area=a.area)
        G = gen_battle(seed, p, src_lv, src_area)
        party = [x.strip() for x in (a.party or "").split(",") if x.strip()]
        for pid in party:
            if not (Path(cdir) / "characters" / f"{pid}.md").exists():
                raise MapError(f"personagem '{pid}' não existe em characters/")
        p.update(party=party, enemies=a.enemies or "", ambush=bool(a.ambush))
        toks = place_tokens(G, p["w"], p["h"], party, parse_enemies(a.enemies), a.ambush, random.Random(seed + 1))
        from rpg import read_meta
        for tk in toks:
            if tk.get("ref"):
                tk["name"] = read_meta(Path(cdir) / "characters" / f"{tk['ref']}.md").get("name", tk["ref"])
        layers = {"grid": ["".join(r) for r in G], "tokens": toks}
        areas, points = [], []
        if a.source:
            for sp in src["points"]:
                if sp.get("level") == ar["level"] and src_area[0] <= sp["x"] < src_area[2] and src_area[1] <= sp["y"] < src_area[3] \
                        and sp["kind"] in ("armadilha", "tesouro", "ponto", "porta_secreta"):
                    q = {k: v for k, v in sp.items() if k not in ("level", "area", "link")}
                    q.update(x=sp["x"] - src_area[0], y=sp["y"] - src_area[1])
                    points.append(q)
    p = {k: v for k, v in p.items()}
    m = {"id": mid, "campaign": Path(cdir).name, "type": t, "name": a.name, "description": a.description or "",
         "public_description": a.public_description or "",
         "generator": {"version": GEN_VERSION, "seed": seed, "params": p},
         "size": {"w": p["w"], "h": p["h"], "unit": unit, "per_cell": round(per, 3), "grid": "quadrada"},
         "files": {}, "areas": areas, "points": points, "connections": [], "discovery": {}, "layers": layers}
    if parent:
        m["connections"].append({"to": parent["id"], "note": "mapa-pai"})
    if a.source:
        m["connections"].append({"to": a.source, "note": f"recorte de {a.area}"})
    m["files"] = _files(m)
    save_map(cdir, m, a.reason or f"gerado ({t})")
    if parent:
        parent["connections"].append({"to": mid, "note": "detalhe"})
        save_map(cdir, parent, f"conectado ao detalhe {mid}")
    render_campaign(cdir)
    print(f"Mapa gerado: {mid} ({t}, seed {seed}) — {len(areas)} áreas, {len(points)} locais")
    print(f"  dados: maps/{mid}.json | mestre: maps/mestre/index.html | jogador: maps/jogador/index.html")


def _files(m):
    sufs = [f".L{i + 1}" for i in range(len(m["layers"]["levels"]))] if m["type"] == "masmorra" else [""]
    return {"dados": f"maps/{m['id']}.json", "mestre": [f"maps/mestre/{m['id']}{s}.svg" for s in sufs],
            "jogador": [f"maps/jogador/{m['id']}{s}.svg" for s in sufs] + ["(só aparece após revelar)"]}


def cmd_list(a):
    cdir = _cdir(a)
    maps = load_all(cdir)
    if not maps:
        print("Nenhum mapa nesta campanha. Gere com 'rpg.py map gen ...' ou /mapa gerar.")
    for m in maps:
        lv = f", {len(m['layers']['levels'])} níveis" if m["type"] == "masmorra" else ""
        print(f"{m['id']:28} {m['type']:9} {m['name']} — {len(m['points'])} locais, {len(m['areas'])} áreas{lv}, "
              f"{len(m['discovery'])} revelados | atualizado {m.get('updated', {}).get('at', '?')} ({m.get('updated', {}).get('reason', '')})")


def cmd_show(a):
    """Lista áreas, locais e tokens de um mapa (visão do mestre), com ids para os outros comandos."""
    m = load_map(_cdir(a), a.map)
    print(f"{m['id']} — {m['name']} ({m['type']}, {m['size']['w']}x{m['size']['h']}, 1 célula = {m['size']['per_cell']:g} {m['size']['unit']}, seed {m['generator']['seed']})")
    for e in _elems(m):
        kind = e.get("kind") or e.get("side")
        if a.kind and kind != a.kind or a.level and e.get("level") != a.level:
            continue
        pos = f" {coord(e['x'], e['y'])} ({e['x']},{e['y']})" if "x" in e else ""
        extra = "".join(f" {k}={e[k]}" for k in ("level", "region", "area", "ref", "link_map") if e.get(k))
        print(f"  {e['id']:44} {kind:13} {e['name']}{pos}{extra}" + (" [SECRETO]" if e.get("secret") else "")
              + (f" [jogador: {m['discovery'][e['id']]}]" if e["id"] in m["discovery"] else ""))


def cmd_where(a):
    cdir = _cdir(a)
    hits = 0
    for m in load_all(cdir):
        for e in _elems(m):
            if a.id in (e["id"], e.get("ref")):
                hits += 1
                lv = f" nível {e['level']}" if e.get("level") else ""
                pos = f" em {coord(e['x'], e['y'])} ({e['x']},{e['y']})" if "x" in e else ""
                print(f"{m['id']}: {e['id']} '{e['name']}'{lv}{pos} status={m['discovery'].get(e['id'], '-')}"
                      + (f" ref={e['ref']}" if e.get("ref") else ""))
    if not hits:
        print(f"'{a.id}' não está em nenhum mapa.")


def cmd_place(a):
    cdir = _cdir(a)
    m = load_map(cdir, a.map)
    if a.ref:
        import world
        if not world.Ctx(cdir).exists(a.ref):
            raise MapError(f"ref '{a.ref}' não existe na campanha")
        dup = next((p for p in m["points"] if p.get("ref") == a.ref), None)
        if dup:
            raise MapError(f"{a.ref} já está neste mapa como {dup['id']} — use 'map set' para alterar")
    if any(slug(p["name"]) == slug(a.name) for p in m["points"]):
        raise MapError(f"já existe um local chamado '{a.name}' neste mapa — não duplico")
    used_ids, _ = _used(cdir)
    pid = None
    if a.ref:  # mesmo lugar já presente em outro mapa → reutiliza o id
        for om in load_all(cdir):
            pid = pid or next((p["id"] for p in om["points"] if p.get("ref") == a.ref), None)
    W, H = m["size"]["w"], m["size"]["h"]
    if a.at:
        x, y = _parse_at(a.at)
    elif a.near:
        ref = find_elem(m, a.near)
        if not ref or "x" not in ref:
            raise MapError(f"'{a.near}' não é um local deste mapa")
        x, y = ref["x"] + 2, ref["y"] + 1
    else:
        raise MapError("informe --at x,y (ou A1) ou --near <poi-id>")
    pt = {"id": pid or _uniq("poi-" + slug(a.name), used_ids, m["id"].removeprefix("map-")), "name": a.name, "kind": a.kind,
          "x": x, "y": y}
    if m["type"] == "masmorra":
        pt["level"] = a.level or (ref.get("level") if a.near else 1)
    for k in ("ref", "note", "gm_note"):
        if getattr(a, k):
            pt[k] = getattr(a, k)
    if a.secret:
        pt["secret"] = True
    if a.radius:
        pt["radius"] = a.radius
    if not (0 <= x < W and 0 <= y < H):
        raise MapError(f"coordenada fora do mapa ({x},{y}); limites 0..{W - 1}, 0..{H - 1}")
    def ok(cx, cy):
        if not _passable(m, dict(pt, x=cx, y=cy)):
            return False
        if m["type"] == "cidade":  # precisa de acesso por rua (mesma regra da validação)
            cells = m["layers"]["cells"]
            return any(0 <= cx + dx < W and 0 <= cy + dy < H and cells[cy + dy][cx + dx] in "rRBpG" for dx, dy in N4 + ((0, 0),))
        return True
    if not ok(x, y):  # procura a célula válida mais próxima
        cells = sorted(((math.hypot(cx - x, cy - y), cx, cy) for cy in range(H) for cx in range(W) if ok(cx, cy)))
        if not cells:
            raise MapError("não há célula válida para o local")
        _, pt["x"], pt["y"] = cells[0]
        print(f"(posição ajustada para a célula válida mais próxima: {pt['x']},{pt['y']})")
    if m["type"] in ("mundo", "regiao"):
        ch = m["layers"]["regions"][pt["y"]][pt["x"]]
        if ch != ".":
            pt["region"] = m["areas"][AC.index(ch)]["id"]
    elif m["type"] == "cidade":
        ch = m["layers"]["districts"][pt["y"]][pt["x"]]
        if ch != ".":
            pt["area"] = m["areas"][AC.index(ch)]["id"]
    m["points"].append(pt)
    if m["type"] in ("mundo", "regiao") and pt["kind"] in ("capital", "cidade", "vila", "fortaleza"):
        _connect_road(m, pt)
    save_map(cdir, m, a.reason or f"adicionado {pt['id']}")
    render_campaign(cdir)
    print(f"Adicionado {pt['id']} '{a.name}' em {m['id']} ({pt['x']},{pt['y']}){' [SECRETO]' if a.secret else ''}")


def _connect_road(m, pt):
    """Novo assentamento ganha estrada até a rede existente da mesma massa de terra."""
    B = m["layers"]["biomes"]
    W, H = m["size"]["w"], m["size"]["h"]
    lm = _landmasses([list(r) for r in B], W, H)
    me = lm.get((pt["x"], pt["y"]))
    road = {tuple(c) for path in m["layers"]["roads"] for c in path}
    targets = sorted(c for c in road if lm.get(c) == me) or sorted(
        (q["x"], q["y"]) for q in m["points"] if q is not pt and q["kind"] in ("capital", "cidade", "vila", "fortaleza")
        and lm.get((q["x"], q["y"])) == me)
    if not targets:
        return
    goal = min(targets, key=lambda c: (math.hypot(c[0] - pt["x"], c[1] - pt["y"]), c))
    path = astar((pt["x"], pt["y"]), goal, W, H, lambda x, y: None if B[y][x] in WATER else LAND_COST[B[y][x]], N8, 1.0)
    if path:
        m["layers"]["roads"].append([list(c) for c in path])


def _parse_at(s):
    s = s.strip()
    mm = re.fullmatch(r"([A-Za-z]+)(\d+)", s)
    if mm:
        col = 0
        for ch in mm.group(1).upper():
            col = col * 26 + ord(ch) - 64
        return col - 1, int(mm.group(2)) - 1
    x, y = s.split(",")
    return int(x), int(y)


def cmd_set(a):
    cdir = _cdir(a)
    m = load_map(cdir, a.map)
    allowed = {"name", "note", "gm_note", "secret", "ref", "faction", "kind", "link_map", "description", "public_description"}
    kv = _parse_kv(a.kv)
    bad = set(kv) - allowed
    if bad:
        raise MapError(f"campos não editáveis: {', '.join(sorted(bad))} (permitidos: {', '.join(sorted(allowed))})")
    target = m if a.elem == m["id"] else find_elem(m, a.elem)
    if target is None:
        raise MapError(f"'{a.elem}' não existe em {m['id']}")
    for k, v in kv.items():
        if v is None:
            target.pop(k, None)
        else:
            target[k] = v
    save_map(cdir, m, a.reason or f"editado {a.elem}: {', '.join(kv)}")
    render_campaign(cdir)
    print(f"{a.elem} atualizado em {m['id']}")


def cmd_connect(a):
    cdir = _cdir(a)
    ma, mb = load_map(cdir, a.a), load_map(cdir, a.b)
    if a.via:
        e = find_elem(ma, a.via)
        if not e:
            raise MapError(f"'{a.via}' não existe em {ma['id']}")
        e["link_map"] = mb["id"]
    for x, y in ((ma, mb), (mb, ma)):
        if not any(c["to"] == y["id"] for c in x["connections"]):
            c = {"to": y["id"], "note": a.note or ""}
            if a.public:
                c["public"] = True
            x["connections"].append(c)
    save_map(cdir, ma, a.reason or f"conectado a {mb['id']}")
    save_map(cdir, mb, a.reason or f"conectado a {ma['id']}")
    render_campaign(cdir)
    print(f"Conectados: {ma['id']} ⇄ {mb['id']}" + (f" (via {a.via})" if a.via else ""))


def cmd_reveal(a):
    cdir = _cdir(a)
    m = load_map(cdir, a.map)
    ids = list(a.ids)
    if a.all:
        ids = [m["id"]] + [e["id"] for e in m["points"] + m["areas"] if not e.get("secret")]
    if not ids:
        raise MapError("informe ids (locais, áreas ou o próprio id do mapa) ou --all")
    for i in ids:
        if i != m["id"] and not find_elem(m, i):
            raise MapError(f"'{i}' não existe em {m['id']}")
    for i in ids:
        if a.hide:
            m["discovery"].pop(i, None)
        else:
            m["discovery"][i] = a.status
    save_map(cdir, m, a.reason or (f"ocultado: {', '.join(ids)}" if a.hide else f"revelado ({a.status}): {', '.join(ids[:5])}"))
    render_campaign(cdir)
    print(("Ocultados" if a.hide else f"Revelados como '{a.status}'") + f": {len(ids)} elemento(s) em {m['id']}")


def cmd_mark(a):
    """Marca área de efeito/ponto num mapa de batalha (não move tokens nem decide resultados)."""
    a.kind = "aoe"
    a.ref = a.note = a.gm_note = None
    a.secret = False
    a.level = None
    a.near = None
    cmd_place(a)


def cmd_validate(a):
    cdir = _cdir(a)
    maps = [load_map(cdir, a.map)] if a.map else load_all(cdir)
    bad = 0
    for m in maps:
        probs = validate(m, cdir)
        bad += bool(probs)
        print(f"{m['id']}: " + ("OK" if not probs else "\n  " + "\n  ".join(probs)))
    if not maps:
        print("Nenhum mapa para validar.")
    raise SystemExit(1 if bad else 0)


def cmd_render(a):
    cdir = _cdir(a)
    w = render_campaign(cdir, png=a.png)
    base = Path(cdir)
    for view in ("mestre", "jogador"):
        print(f"{view}: {len(w[view])} arquivos → {(mdir(cdir) / view / 'index.html').resolve().as_uri()}")
    if a.png:
        if w["png"]:
            print(f"PNG: {len(w['png'])} arquivos gerados (" + ", ".join(str(p.relative_to(base)) for p in w["png"][:4]) + ("…)" if len(w["png"]) > 4 else ")"))
        else:
            print("PNG NÃO gerado: nenhum conversor disponível (rsvg-convert, inkscape ou Chrome/Chromium). Veja o README.")


def cmd_open(a):
    cdir = _cdir(a)
    if not (mdir(cdir) / "mestre" / "index.html").exists():
        render_campaign(cdir)
    for view in ("mestre", "jogador"):
        print(f"{view}: {(mdir(cdir) / view / 'index.html').resolve().as_uri()}")


def _wrap(f):
    def g(a):
        try:
            f(a)
        except MapError as e:
            from rpg import die
            die(str(e))
    return g


def register(sp):
    mp = sp.add_parser("map", help="mapas: gerar, listar, revelar, posicionar, conectar, validar, exportar")
    s = mp.add_subparsers(dest="mcmd", required=True)
    g = s.add_parser("gen", help="gera mapa: mundo | regiao | cidade | masmorra | batalha")
    g.add_argument("kind", choices=TYPES)
    g.add_argument("id", help="id do mapa (ex.: map-continente)")
    g.add_argument("--name", required=True)
    g.add_argument("--description", help="descrição (mestre)")
    g.add_argument("--public-description", help="descrição que os jogadores podem ver")
    g.add_argument("--seed", type=int)
    g.add_argument("--w", type=int); g.add_argument("--h", type=int)
    g.add_argument("--continents", type=int); g.add_argument("--land", type=float); g.add_argument("--regions", type=int)
    g.add_argument("--density", type=float, help="assentamentos por 1000 células de terra")
    g.add_argument("--ruins", type=int); g.add_argument("--islands", type=float)
    g.add_argument("--climate", choices=sorted(CLIMATE)); g.add_argument("--km", type=float, help="km por célula (mundo)")
    g.add_argument("--parent", help="região: mapa de mundo de origem"); g.add_argument("--area", help="região: reg-id; batalha: sala")
    g.add_argument("--bbox", help="região: x0,y0,x1,y1 no mapa-pai")
    g.add_argument("--districts", type=int); g.add_argument("--gates", type=int); g.add_argument("--river", choices=["sim", "nao"])
    g.add_argument("--taverns", type=int); g.add_argument("--temples", type=int)
    g.add_argument("--levels", type=int); g.add_argument("--rooms", type=int); g.add_argument("--traps", type=int)
    g.add_argument("--encounters", type=int); g.add_argument("--m", type=float, help="metros por célula (cidade/masmorra/batalha)")
    g.add_argument("--env", choices=ENVS); g.add_argument("--party", help="ids pc-* separados por vírgula")
    g.add_argument("--enemies", help='ex.: "Capanga x3, Chefe"'); g.add_argument("--ambush", action="store_true")
    g.add_argument("--from", dest="source", help="batalha: recorta sala de uma masmorra")
    g.add_argument("--reason"); g.add_argument("--campaign")
    g.set_defaults(f=_wrap(cmd_gen))
    c = s.add_parser("list", help="lista mapas"); c.add_argument("--campaign"); c.set_defaults(f=_wrap(cmd_list))
    c = s.add_parser("show", help="lista áreas/locais/tokens de um mapa com ids (mestre)")
    c.add_argument("map"); c.add_argument("--kind"); c.add_argument("--level", type=int); c.add_argument("--campaign")
    c.set_defaults(f=_wrap(cmd_show))
    c = s.add_parser("where", help="onde um local/entidade aparece nos mapas"); c.add_argument("id"); c.add_argument("--campaign")
    c.set_defaults(f=_wrap(cmd_where))
    c = s.add_parser("place", help="adiciona local a um mapa existente (sem duplicar)")
    c.add_argument("map"); c.add_argument("--name", required=True); c.add_argument("--kind", default="ponto")
    c.add_argument("--at", help="x,y ou A1"); c.add_argument("--near", help="poi-id de referência"); c.add_argument("--level", type=int)
    c.add_argument("--ref", help="loc-*/npc-* vinculado"); c.add_argument("--note"); c.add_argument("--gm-note")
    c.add_argument("--secret", action="store_true"); c.add_argument("--radius", type=float); c.add_argument("--reason"); c.add_argument("--campaign")
    c.set_defaults(f=_wrap(cmd_place))
    c = s.add_parser("mark", help="marca área de efeito num mapa de batalha")
    c.add_argument("map"); c.add_argument("--name", required=True); c.add_argument("--at", required=True)
    c.add_argument("--radius", type=float, default=1); c.add_argument("--reason"); c.add_argument("--campaign")
    c.set_defaults(f=_wrap(cmd_mark))
    c = s.add_parser("set", help="edita campos de um elemento (chave=valor)")
    c.add_argument("map"); c.add_argument("elem"); c.add_argument("kv", nargs="+"); c.add_argument("--reason"); c.add_argument("--campaign")
    c.set_defaults(f=_wrap(cmd_set))
    c = s.add_parser("connect", help="conecta dois mapas (opcionalmente via um local)")
    c.add_argument("a"); c.add_argument("b"); c.add_argument("--via"); c.add_argument("--note")
    c.add_argument("--public", action="store_true", help="jogadores sabem da ligação"); c.add_argument("--reason"); c.add_argument("--campaign")
    c.set_defaults(f=_wrap(cmd_connect))
    c = s.add_parser("reveal", help="revela/oculta elementos no mapa dos jogadores")
    c.add_argument("map"); c.add_argument("ids", nargs="*"); c.add_argument("--status", choices=STATUSES, default="conhecido")
    c.add_argument("--all", action="store_true", help="revela tudo que não é secreto"); c.add_argument("--hide", action="store_true")
    c.add_argument("--reason"); c.add_argument("--campaign")
    c.set_defaults(f=_wrap(cmd_reveal))
    c = s.add_parser("validate", help="valida estrutura, conectividade, limites e referências")
    c.add_argument("map", nargs="?"); c.add_argument("--campaign"); c.set_defaults(f=_wrap(cmd_validate))
    c = s.add_parser("render", help="regenera SVG/HTML (mestre e jogador); --png opcional")
    c.add_argument("--png", action="store_true"); c.add_argument("--campaign"); c.set_defaults(f=_wrap(cmd_render))
    c = s.add_parser("open", help="mostra os caminhos dos visualizadores"); c.add_argument("--campaign"); c.set_defaults(f=_wrap(cmd_open))
