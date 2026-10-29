"""Processa exportações do Google Takeout (YouTube) e gera o relatório de consumo infantil.

Uso:
  python processar.py            processa o que estiver em entrada/ uma vez
  python processar.py --monitorar  fica vigiando entrada/ e processa sempre que algo novo chegar
  python processar.py --refazer    gera o relatório de novo com a base já acumulada
"""
import html, json, os, re, shutil, sys, time, zipfile, statistics as st, urllib.request, urllib.error, webbrowser
import concurrent.futures as cf
from collections import Counter, defaultdict
from datetime import datetime, date, timedelta, timezone

SIS = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(SIS)
ENTRADA, SAIDA, PROC = (os.path.join(RAIZ, p) for p in ('entrada', 'relatorios', 'processados'))
BASE, CACHE = os.path.join(SIS, 'base.json'), os.path.join(SIS, 'cache_titulos.json')
CFG = json.load(open(os.path.join(SIS, 'config.json'), encoding='utf-8'))
for _p in (ENTRADA, SAIDA, PROC):
    os.makedirs(_p, exist_ok=True)

def log(*a):
    print(time.strftime('%H:%M:%S'), *a, flush=True)

# ---------------------------------------------------------------- leitura do Takeout

NOMES_HIST = ('histórico-de-visualização', 'watch-history', 'historial de reproducciones')
PASTAS_KIDS = ('crianças', 'kids', 'niños', 'children')
MESES = {'jan': 1, 'fev': 2, 'feb': 2, 'mar': 3, 'abr': 4, 'apr': 4, 'mai': 5, 'may': 5, 'jun': 6, 'jul': 7,
         'ago': 8, 'aug': 8, 'set': 9, 'sep': 9, 'out': 10, 'oct': 10, 'nov': 11, 'dez': 12, 'dec': 12}
CELULA = re.compile(r'content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1">(.*?)</div>', re.S)
DATA_PT = re.compile(r'(\d{1,2}) de (\w{3})\.? de (\d{4}),? (\d{1,2}):(\d{2}):(\d{2})')
DATA_EN = re.compile(r'(\w{3}) (\d{1,2}), (\d{4}),? (\d{1,2}):(\d{2}):(\d{2})\s*([AP]M)?')

def perfil_do_caminho(caminho):
    partes = [p for p in re.split(r'[\\/]', caminho) if p]
    for i, p in enumerate(partes[:-1]):
        if p.lower() in PASTAS_KIDS and i + 1 < len(partes) - 1:
            return partes[i + 1], True
    return 'Principal', False

def eh_historico(nome):
    base = os.path.splitext(os.path.basename(nome))[0].lower()
    return base in NOMES_HIST and nome.lower().endswith(('.html', '.json'))

def ler_data(txt):
    m = DATA_PT.search(txt)
    if m and m[2].lower() in MESES:
        return datetime(int(m[3]), MESES[m[2].lower()], int(m[1]), int(m[4]), int(m[5]), int(m[6]))
    m = DATA_EN.search(txt)
    if m and m[1].lower() in MESES:
        h = int(m[4])
        if m[7]:
            h = h % 12 + (12 if m[7] == 'PM' else 0)
        return datetime(int(m[3]), MESES[m[1].lower()], int(m[2]), h, int(m[5]), int(m[6]))

def parse_html(s, perfil, kids):
    out = []
    for m in CELULA.finditer(s):
        c = m.group(1)
        if not c.strip():
            continue
        dt = ler_data(c)
        links = re.findall(r'<a href="([^"]+)">(.*?)</a>', c)
        if not dt or not links or 'watch?v=' not in links[0][0]:
            continue  # anúncios, posts, visitas a canais
        canal = html.unescape(links[1][1]) if len(links) > 1 and '/channel/' in links[1][0] else ''
        titulo = html.unescape(links[0][1])
        out.append(evento(perfil, kids, dt, links[0][0], '' if titulo.startswith('http') else titulo, canal))
    return out

def parse_json(s, perfil, kids):
    out = []
    for e in json.loads(s):
        url = e.get('titleUrl', '')
        if 'watch?v=' not in url or 'details' in e:
            continue
        dt = datetime.fromisoformat(e['time'].replace('Z', '+00:00')).astimezone().replace(tzinfo=None)
        titulo = re.sub(r'^(Watched|Assistiu a|Assistiu|Viu)\s+', '', e.get('title', ''))
        canal = (e.get('subtitles') or [{}])[0].get('name', '')
        out.append(evento(perfil, kids, dt, url, '' if titulo.startswith('http') else titulo, canal))
    return out

def evento(perfil, kids, dt, url, titulo, canal):
    vid = re.search(r'v=([\w-]{11})', html.unescape(url))
    return {'perfil': perfil, 'kids_perfil': kids, 'dt': dt.isoformat(), 'vid': vid[1] if vid else url, 'titulo': titulo, 'canal': canal}

def ler_arquivo(nome, conteudo):
    perfil, kids = perfil_do_caminho(nome)
    s = conteudo.decode('utf-8', 'ignore')
    ev = parse_json(s, perfil, kids) if nome.lower().endswith('.json') else parse_html(s, perfil, kids)
    log(f'  {perfil}: {len(ev)} vídeos em {os.path.basename(nome)}')
    return ev

def ler_entrada(caminho):
    """Lê .zip (sem extrair) ou pasta e devolve os eventos de todos os perfis encontrados."""
    ev = []
    if caminho.lower().endswith('.zip'):
        with zipfile.ZipFile(caminho) as z:
            for n in z.namelist():
                if eh_historico(n):
                    ev += ler_arquivo(n, z.read(n))
    elif os.path.isdir(caminho):
        for raiz, _, arqs in os.walk(caminho):
            for a in arqs:
                p = os.path.join(raiz, a)
                if eh_historico(p):
                    ev += ler_arquivo(os.path.relpath(p, caminho), open(p, 'rb').read())
                elif a.lower().endswith('.zip'):
                    ev += ler_entrada(p)
    elif eh_historico(caminho):
        ev += ler_arquivo(caminho, open(caminho, 'rb').read())
    return ev

# ---------------------------------------------------------------- títulos dos perfis infantis (oEmbed)

def completar_titulos(ev):
    cache = json.load(open(CACHE, encoding='utf-8')) if os.path.exists(CACHE) else {}
    # no perfil principal, vídeo sem título é vídeo removido/privado; só os perfis Kids vêm sem título por padrão
    faltam = sorted({e['vid'] for e in ev if not e['titulo'] and e['kids_perfil'] and e['vid'] not in cache})
    if faltam and CFG.get('buscar_titulos_online', True):
        log(f'Buscando título e canal de {len(faltam)} vídeos sem título (perfis infantis)...')
        bloqueado = False

        def get(v):
            nonlocal bloqueado
            if bloqueado:
                return v, None
            try:
                u = 'https://www.youtube.com/oembed?format=json&url=https://www.youtube.com/watch?v=' + v
                d = json.loads(urllib.request.urlopen(u, timeout=20).read().decode('utf-8'))
                return v, {'titulo': d.get('title', ''), 'canal': d.get('author_name', '')}
            except urllib.error.HTTPError as e:
                if e.code in (401, 403, 404):
                    return v, {'titulo': '(vídeo indisponível)', 'canal': '(indisponível)'}
                if e.code == 429:
                    bloqueado = True
                return v, None
            except Exception:
                return v, None
        with cf.ThreadPoolExecutor(4) as ex:
            for n, (v, m) in enumerate(ex.map(get, faltam), 1):
                if m:
                    cache[v] = m
                if n % 100 == 0:
                    log(f'  {n}/{len(faltam)}')
        if bloqueado:
            log('  O YouTube limitou as consultas; o restante fica para a próxima execução.')
        json.dump(cache, open(CACHE, 'w', encoding='utf-8'), ensure_ascii=False)
    for e in ev:
        if not e['titulo'] and not e['kids_perfil']:
            e['titulo'], e['canal'] = '(vídeo removido)', e['canal'] or '(sem canal)'
        elif not e['titulo']:
            m = cache.get(e['vid'])
            e['titulo'], e['canal'] = (m['titulo'], m['canal']) if m else ('(sem título)', '(sem canal)')

# ---------------------------------------------------------------- classificação

KW = re.compile(CFG['palavras_infantis'], re.I)
CANAIS = {c.strip() for c in CFG['canais_infantis']}
EXCL = {c.strip() for c in CFG['canais_excluidos']}
CATS = [(c['nome'], {x.strip() for x in c['canais']}, re.compile(c['palavras'], re.I)) for c in CFG['categorias']]

def eh_infantil(e):
    if e['kids_perfil']:
        return True
    c = e['canal'].strip()
    if c in EXCL:
        return False
    return c in CANAIS or bool(KW.search(e['titulo']))

def categoria(e):
    c = e['canal'].strip()
    for nome, canais, _ in CATS:
        if c in canais:
            return nome
    for nome, _, kw in CATS:
        if kw.search(e['titulo']):
            return nome
    return CFG['categoria_padrao']

# ---------------------------------------------------------------- análise

DOW = ['seg', 'ter', 'qua', 'qui', 'sex', 'sáb', 'dom']
MES = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez']

def hm(m):
    m = round(m)
    return f'{m // 60}h{m % 60:02d}' if m >= 60 else f'{m} min'

def f1(x):
    return f'{x:.1f}'.replace('.', ',')

def mes_nome(k):
    return f'{MES[int(k[5:]) - 1]}/{k[2:4]}'

def analisar(ev):
    cap, ult = CFG['limite_gap_min'] * 60, CFG['ultimo_video_min'] * 60
    ref = CFG['referencia_min_dia']
    for e in ev:
        e['t'] = datetime.fromisoformat(e['dt'])
    perfis = sorted({e['perfil'] for e in ev}, key=lambda p: (p == 'Principal', p))
    kids_perfis = [p for p in perfis if any(e['kids_perfil'] for e in ev if e['perfil'] == p)]
    for p in perfis:
        x = sorted((e for e in ev if e['perfil'] == p), key=lambda e: e['t'])
        for a, b in zip(x, x[1:] + [None]):
            g = (b['t'] - a['t']).total_seconds() if b else 1e9
            a['seg'] = g if 0 < g <= cap else ult
            a['fim'] = not (0 < g <= cap)
    for e in ev:
        e['kids'] = eh_infantil(e)
        if e['kids']:
            e['cat'] = categoria(e)

    FIM = max(e['t'] for e in ev).date()
    INI = FIM - timedelta(CFG['janela_dias'] - 1)
    dias = (FIM - INI).days + 1
    P1, P0 = INI - timedelta(1), INI - timedelta(dias)
    k3 = [e for e in ev if e['kids'] and INI <= e['t'].date() <= FIM]
    prev = [e for e in ev if e['kids'] and P0 <= e['t'].date() <= P1]
    if not k3:
        raise SystemExit('Nenhum conteúdo infantil encontrado no período.')
    H = lambda lst: sum(e['seg'] for e in lst) / 3600
    tot_h = H(k3)
    lbl = lambda p: p if p == 'Principal' else f'{p} (Kids)'
    D = {'perfis': [lbl(p) for p in perfis]}

    # série diária por perfil
    datas = [INI + timedelta(i) for i in range(dias)]
    dmin = defaultdict(lambda: defaultdict(float))
    for e in k3:
        dmin[e['t'].date()][e['perfil']] += e['seg'] / 60
    mins = {d: sum(dmin[d].values()) for d in datas}
    ativos = [m for m in mins.values() if m > 0]
    D['diario'] = {'labels': [d.strftime('%d/%m') for d in datas], 'series': {lbl(p): [round(dmin[d][p], 1) for d in datas] for p in perfis}, 'ref': ref}

    # semanal
    semanas = sorted({d - timedelta(d.weekday()) for d in datas})
    D['semanal'] = {'labels': [s.strftime('%d/%m') for s in semanas],
                    'series': {lbl(p): [round(sum(dmin[d][p] for d in datas if d - timedelta(d.weekday()) == s) / 60, 1) for s in semanas] for p in perfis}}
    sem_tot = [sum(D['semanal']['series'][lbl(p)][i] for p in perfis) for i in range(len(semanas))]

    # mensal dentro da janela
    meses = sorted({d.strftime('%Y-%m') for d in datas})
    linhas_mes = []
    for m in meses:
        dm = [d for d in datas if d.strftime('%Y-%m') == m]
        em = [e for e in k3 if e['t'].strftime('%Y-%m') == m]
        h = H(em)
        linhas_mes.append({'mes': mes_nome(m) + (f' ({dm[0].day}–{dm[-1].day})' if len(dm) < 28 else ''), 'h': round(h, 1), 'media': hm(h * 60 / len(dm)),
                           'n': len(em), 'ativos': len({e['t'].date() for e in em}), 'pct_principal': round(H([e for e in em if e['perfil'] == 'Principal']) / h * 100) if h else 0,
                           'media_min': h * 60 / len(dm), 'dias': len(dm)})
    D['mensal'] = linhas_mes

    # hora e dia da semana
    hh = defaultdict(lambda: defaultdict(float))
    for e in k3:
        hh[e['t'].hour][e['perfil']] += e['seg'] / 60
    D['hora'] = {'labels': [f'{h}h' for h in range(24)], 'series': {lbl(p): [round(hh[h][p] / dias, 1) for h in range(24)] for p in perfis}}
    pdow = {i: [mins[d] for d in datas if d.weekday() == i] for i in range(7)}
    D['dow'] = [round(st.mean(pdow[i]), 1) if pdow[i] else 0 for i in range(7)]

    # dias úteis × fim de semana
    U = [mins[d] for d in datas if d.weekday() < 5]
    F = [mins[d] for d in datas if d.weekday() >= 5]
    nu, nf = max(len(U), 1), max(len(F), 1)
    hu, hf = defaultdict(float), defaultdict(float)
    for e in k3:
        (hu if e['t'].weekday() < 5 else hf)[e['t'].hour] += e['seg'] / 60
    faixa = lambda hx, a, b: sum(hx[h] for h in range(a, b)) / (sum(hx.values()) or 1) * 100
    mes_uf = []
    for m in meses:
        u = [mins[d] for d in datas if d.strftime('%Y-%m') == m and d.weekday() < 5]
        f = [mins[d] for d in datas if d.strftime('%Y-%m') == m and d.weekday() >= 5]
        mes_uf.append({'mes': mes_nome(m), 'util': round(st.mean(u), 1) if u else None, 'fds': round(st.mean(f), 1) if f else None, 'nu': len(u)})
    inicio_tarde = []
    for d in datas:
        if d.weekday() < 5:
            t = [e['t'] for e in k3 if e['t'].date() == d and e['t'].hour >= 15]
            if t:
                inicio_tarde.append(min(t).hour * 60 + min(t).minute)
    manha = len({e['t'].date() for e in k3 if e['t'].weekday() < 5 and 6 <= e['t'].hour < 9})
    catu, catf = defaultdict(float), defaultdict(float)
    cu = defaultdict(float)
    for e in k3:
        if e['t'].weekday() < 5:
            catu[e['cat']] += e['seg']; cu[e['canal']] += e['seg'] / 3600
        else:
            catf[e['cat']] += e['seg']
    pct = lambda dct, k: round(dct.get(k, 0) / (sum(dct.values()) or 1) * 100)
    por_dia = []
    for i in range(7):
        v = pdow[i]
        if v:
            por_dia.append({'dow': DOW[i], 'media': hm(st.mean(v)), 'mediana': hm(st.median(v)), 'ativos': f'{sum(1 for x in v if x > 0)}/{len(v)}',
                            'acima': sum(1 for x in v if x >= ref), 'max': hm(max(v)), 'media_min': st.mean(v)})
    pico_u = max(range(24), key=lambda h: sum(hu[x % 24] for x in range(h, h + 4)))
    D['util'] = {
        'mes': mes_uf, 'hora': {'util': [round(hu[h] / nu, 1) for h in range(24)], 'fds': [round(hf[h] / nf, 1) for h in range(24)]},
        'por_dia': por_dia, 'canais': [(c, round(h, 1)) for c, h in sorted(cu.items(), key=lambda x: -x[1])[:8]]}

    # canais, categorias, repetição
    ch = defaultdict(lambda: {'s': 0, 'n': 0, **{p: 0 for p in perfis}})
    for e in k3:
        c = ch[e['canal'] or '(sem canal)']; c['s'] += e['seg']; c['n'] += 1; c[e['perfil']] += 1
    D['canais'] = [{'canal': k, 'h': round(v['s'] / 3600, 1), 'n': v['n'], 'por_perfil': [v[p] for p in perfis]} for k, v in sorted(ch.items(), key=lambda x: -x[1]['s'])[:20]]
    D['n_canais'] = len(ch)
    cc = defaultdict(float)
    for e in k3:
        cc[e['cat']] += e['seg']
    D['categorias'] = [{'cat': k, 'h': round(v / 3600, 1), 'pct': round(v / 3600 / tot_h * 100)} for k, v in sorted(cc.items(), key=lambda x: -x[1])]
    rep = Counter(e['vid'] for e in k3)
    tit = {e['vid']: (e['titulo'], e['canal']) for e in k3}
    D['repetidos'] = [{'titulo': tit[v][0], 'canal': tit[v][1], 'n': n} for v, n in rep.most_common(10) if n > 1]

    # sessões
    ses = []
    for p in perfis:
        cur = []
        for e in sorted((e for e in ev if e['perfil'] == p and INI <= e['t'].date() <= FIM), key=lambda e: e['t']):
            cur.append(e)
            if e['fim']:
                if sum(1 for y in cur if y['kids']) / len(cur) >= .5:
                    ses.append({'perfil': lbl(p), 't': cur[0]['t'], 'min': sum(y['seg'] for y in cur) / 60, 'n': len(cur)})
                cur = []
    D['sessoes'] = [{'ini': f"{DOW[s['t'].weekday()]} {s['t']:%d/%m %H:%M}", 'perfil': s['perfil'], 'dur': hm(s['min']), 'n': s['n']} for s in sorted(ses, key=lambda s: -s['min'])[:8]]

    # tendência histórica
    tm = defaultdict(lambda: defaultdict(float))
    for e in ev:
        tm[e['t'].strftime('%Y-%m')][e['perfil'] if e['kids'] else '_adulto'] += e['seg'] / 3600
    tms = sorted(tm)
    D['tendencia'] = {'labels': [mes_nome(m) for m in tms], 'series': {lbl(p): [round(tm[m][p], 1) for m in tms] for p in perfis},
                      'adulto': [round(tm[m]['_adulto'], 1) for m in tms]}

    # ---- KPIs e conclusões (texto gerado a partir dos números)
    ult_mes = linhas_mes[-1]
    pct_princ = round(H([e for e in k3 if e['perfil'] == 'Principal']) / tot_h * 100)
    pct_princ_prev = round(H([e for e in prev if e['perfil'] == 'Principal']) / H(prev) * 100) if prev else None
    D['kpis'] = [
        [f'{round(tot_h)} h', f'de conteúdo infantil no período ({len(k3)} vídeos)'],
        [hm(sum(mins.values()) / dias), 'média por dia (todos os dias)'],
        [ult_mes['media'], f"média por dia em {ult_mes['mes']}"],
        [f'{len(ativos)} de {dias}', 'dias com algum consumo'],
        [str(sum(1 for m in mins.values() if m >= ref)), f'dias acima de {hm(ref)}'],
    ]
    if kids_perfis:
        D['kpis'].append([f'{pct_princ}%', 'assistido no perfil principal (fora do YouTube Kids)'])
    ins = []
    if kids_perfis and pct_princ_prev is not None:
        if pct_princ - pct_princ_prev >= 20:
            ins.append(['w', f'<b>O consumo infantil migrou para o perfil principal.</b> No período anterior, {pct_princ_prev}% das horas infantis eram no perfil principal; agora são {pct_princ}% — fora dos filtros do YouTube Kids e misturado às recomendações do adulto.'])
        elif pct_princ_prev - pct_princ >= 20:
            ins.append(['g', f'<b>O consumo voltou para o perfil Kids.</b> A parcela no perfil principal caiu de {pct_princ_prev}% para {pct_princ}%.'])
        elif pct_princ >= 50:
            ins.append(['w', f'<b>A maior parte ({pct_princ}%) do consumo infantil acontece no perfil principal</b>, fora dos filtros do YouTube Kids.'])
    q = max(1, min(4, len(sem_tot) // 3))
    ini_s, fim_s = st.mean(sem_tot[:q]), st.mean(sem_tot[-q - 1:-1] if len(sem_tot) > q + 1 else sem_tot[-q:])
    if fim_s > ini_s * 1.5:
        ins.append(['w', f'<b>Tendência de alta.</b> A carga semanal passou de ~{f1(ini_s)} h nas primeiras semanas para ~{f1(fim_s)} h nas mais recentes. Em {ult_mes["mes"]}, a média é de {ult_mes["media"]} por dia (referência: {hm(ref)}).'])
    elif fim_s < ini_s / 1.5:
        ins.append(['g', f'<b>Tendência de queda.</b> A carga semanal caiu de ~{f1(ini_s)} h para ~{f1(fim_s)} h. Em {ult_mes["mes"]}, a média é de {ult_mes["media"]} por dia.'])
    else:
        ins.append(['', f'<b>Carga estável ao longo do período</b>, entre ~{f1(ini_s)} h e ~{f1(fim_s)} h por semana. Em {ult_mes["mes"]}, {ult_mes["media"]} por dia.'])
    if prev:
        var = (tot_h / H(prev) - 1) * 100
        ins.append(['w' if var > 25 else 'g' if var < -25 else '', f'<b>Comparado ao período anterior de mesma duração</b> ({P0:%d/%m}–{P1:%d/%m}): {f1(H(prev))} h → {f1(tot_h)} h ({"+" if var >= 0 else ""}{round(var)}%).'])
    top = D['canais'][0]
    ins.append(['', f'<b>{top["canal"]} é o canal dominante:</b> {f1(top["h"])} h, {round(top["h"] / tot_h * 100)}% de todo o tempo infantil. Ao todo foram {D["n_canais"]} canais diferentes.'])
    pico = max(range(24), key=lambda h: sum(sum(hh[x % 24].values()) for x in range(h, h + 4)))
    pct_pico = round(sum(sum(hh[x % 24].values()) for x in range(pico, pico + 4)) / (tot_h * 60) * 100)
    pct_noite = round(sum(sum(hh[h].values()) for h in list(range(21, 24)) + list(range(0, 6))) / (tot_h * 60) * 100)
    ins.append(['w' if pct_noite >= 15 else '', f'<b>Horário de pico: {pico}h–{(pico + 4) % 24}h</b>, com {pct_pico}% do tempo. Depois das 21h e de madrugada: {pct_noite}%.'])
    mu, mf = st.mean(U) if U else 0, st.mean(F) if F else 0
    ins.append(['', f'<b>Fim de semana × dias úteis:</b> {hm(mf)}/dia no sábado e domingo contra {hm(mu)}/dia em dias úteis.'])
    if ses:
        longas = sum(1 for s in ses if s['min'] >= 60)
        ins.append(['w' if longas >= 10 else '', f'<b>Sessões:</b> {len(ses)} sessões predominantemente infantis, duração típica de {hm(st.median(s["min"] for s in ses))}; {longas} passaram de 1 hora.'])
    yt = next((c for c in D['categorias'] if c['cat'].startswith('Youtubers')), None)
    ins.append(['', f'<b>Tipo de conteúdo:</b> ' + ', '.join(f'{c["cat"].lower()} {c["pct"]}%' for c in D['categorias']) + '.'])
    if yt and yt['pct'] >= 20:
        ins.append(['w', f'<b>Youtubers mirins e brinquedos já ocupam {yt["pct"]}% do tempo.</b>'])
    if D['repetidos']:
        ins.append(['', f'<b>Repetição:</b> {len(k3)} visualizações para {len(rep)} vídeos distintos; o mais repetido foi visto {D["repetidos"][0]["n"]} vezes.'])
    D['insights'] = ins

    mu_ini = next((m['util'] for m in mes_uf if m['util'] is not None and m['nu'] >= 10), mes_uf[0]['util'] or 0)
    mu_fim = mes_uf[-1]['util'] or 0
    D['util']['kpis'] = [
        [hm(mu), f'média por dia útil (fim de semana: {hm(mf)})'],
        [hm(mu_fim), f'por dia útil em {mes_uf[-1]["mes"]}'],
        [f'{sum(1 for m in U if m > 0)} de {len(U)}', 'dias úteis com consumo'],
        [str(sum(1 for m in U if m >= ref)), f'dias úteis acima de {hm(ref)}'],
        [f'{round(faixa(hu, pico_u, pico_u + 4))}%', f'do tempo de dia útil entre {pico_u}h e {pico_u + 4}h'],
    ]
    ui = []
    if mu_ini and mu_fim > mu_ini * 1.5:
        ui.append(['w', f'<b>Nos dias úteis o consumo cresceu:</b> de {hm(mu_ini)}/dia no primeiro mês para {hm(mu_fim)}/dia no último.'])
    elif mu_ini and mu_fim < mu_ini / 1.5:
        ui.append(['g', f'<b>Nos dias úteis o consumo caiu:</b> de {hm(mu_ini)}/dia para {hm(mu_fim)}/dia.'])
    vazio = [h for h in range(8, 17) if hu[h] / nu < 0.5]
    txt_vazio = f'quase nada entre {vazio[0]}h e {vazio[-1] + 1}h; ' if len(vazio) >= 4 else ''
    hora_ini = hm(st.median(inicio_tarde)) if inicio_tarde else '—'
    ui.append(['', f'<b>Rotina de dia útil:</b> {txt_vazio}o primeiro vídeo da tarde costuma começar por volta das {hora_ini}, '
               f'e {round(faixa(hu, pico_u, pico_u + 4))}% do tempo fica entre {pico_u}h e {pico_u + 4}h. Houve uso entre 6h e 9h em {manha} dias úteis.'])
    pd_ord = sorted([p for p in por_dia if DOW.index(p['dow']) < 5], key=lambda p: -p['media_min'])
    if pd_ord:
        ui.append(['', f'<b>Dias úteis mais pesados:</b> {pd_ord[0]["dow"]} ({pd_ord[0]["media"]}) e {pd_ord[1]["dow"]} ({pd_ord[1]["media"]}); mais leves: {pd_ord[-1]["dow"]} ({pd_ord[-1]["media"]}).'])
    dif = sorted(cc, key=lambda k: -abs(pct(catu, k) - pct(catf, k)))[:2]
    ui.append(['', '<b>O conteúdo muda conforme o dia:</b> ' + '; '.join(f'{k.lower()}: {pct(catu, k)}% nos dias úteis × {pct(catf, k)}% no fim de semana' for k in dif) + '.'])
    D['util']['insights'] = ui
    for p in D['util']['por_dia']:
        p.pop('media_min')
    for m in D['mensal']:
        m.pop('media_min'); m.pop('dias')

    D['meta'] = {'inicio': f'{INI:%d/%m/%Y}', 'fim': f'{FIM:%d/%m/%Y}', 'dias': dias, 'gerado': datetime.now().strftime('%d/%m/%Y %H:%M'),
                 'perfis_kids': kids_perfis, 'cap': CFG['limite_gap_min'], 'ult': CFG['ultimo_video_min'], 'ref': ref,
                 'sem_titulo': sum(1 for e in k3 if e['titulo'] in ('(sem título)', '(vídeo indisponível)')),
                 'historico_desde': f"{min(e['t'] for e in ev):%d/%m/%Y}", 'total_videos': len(ev)}
    for e in ev:
        e.pop('t', None)
    return D

# ---------------------------------------------------------------- orquestração

def carregar_base():
    return json.load(open(BASE, encoding='utf-8')) if os.path.exists(BASE) else []

def gerar_relatorio(ev):
    D = analisar([dict(e) for e in ev])
    tpl = open(os.path.join(SIS, 'template.html'), encoding='utf-8').read()
    dados = json.dumps(D, ensure_ascii=False, default=str).replace('</', '<\\/')
    nome = f"relatorio_{datetime.now():%Y-%m-%d_%H%M}.html"
    caminho = os.path.join(SAIDA, nome)
    open(caminho, 'w', encoding='utf-8').write(tpl.replace('__DATA__', dados))
    shutil.copy(caminho, os.path.join(RAIZ, 'ÚLTIMO RELATÓRIO.html'))
    log(f'Relatório gerado: relatorios/{nome}')
    return caminho

def itens_entrada():
    return [os.path.join(ENTRADA, n) for n in os.listdir(ENTRADA) if not n.startswith(('.', '~')) and n.lower() != 'coloque aqui o takeout.txt']

def processar(abrir=True):
    itens = itens_entrada()
    if not itens:
        log('Nada em entrada/.')
        return None
    novos = []
    for it in itens:
        log(f'Lendo {os.path.basename(it)}')
        novos += ler_entrada(it)
    if not novos:
        log('Nenhum histórico de visualização encontrado. Confira se o Takeout inclui "YouTube e YouTube Music > histórico".')
        return None
    base = carregar_base()
    chaves = {(e['perfil'], e['dt'], e['vid']) for e in base}
    add = [e for e in novos if (e['perfil'], e['dt'], e['vid']) not in chaves]
    base += add
    log(f'{len(add)} vídeos novos adicionados à base ({len(base)} no total).')
    completar_titulos(base)
    json.dump(base, open(BASE, 'w', encoding='utf-8'), ensure_ascii=False)
    destino = os.path.join(PROC, datetime.now().strftime('%Y-%m-%d_%H%M%S'))
    os.makedirs(destino, exist_ok=True)
    for it in itens:
        shutil.move(it, destino)
    caminho = gerar_relatorio(base)
    if abrir:
        webbrowser.open('file:///' + caminho.replace('\\', '/'))
    return caminho

def tamanho(it):
    if os.path.isfile(it):
        return os.path.getsize(it)
    return sum(os.path.getsize(os.path.join(r, a)) for r, _, aa in os.walk(it) for a in aa)

def monitorar():
    log('Vigiando a pasta entrada/ (feche esta janela para parar).')
    anterior = None
    while True:
        itens = itens_entrada()
        estado = sorted((it, tamanho(it)) for it in itens) if itens else None
        if estado and estado == anterior:  # tamanho estável entre duas checagens = cópia terminou
            try:
                processar()
            except Exception as e:
                log('ERRO:', e)
            anterior = None
            log('Vigiando a pasta entrada/...')
        else:
            anterior = estado
        time.sleep(10)

if __name__ == '__main__':
    if '--monitorar' in sys.argv:
        monitorar()
    elif '--refazer' in sys.argv:
        base = carregar_base()
        if not base:
            raise SystemExit('Base vazia: coloque um Takeout em entrada/ primeiro.')
        completar_titulos(base)
        json.dump(base, open(BASE, 'w', encoding='utf-8'), ensure_ascii=False)
        webbrowser.open('file:///' + gerar_relatorio(base).replace('\\', '/'))
    else:
        processar()
