"""Gera um Takeout FICTÍCIO para testar o sistema sem dados reais.

Uso:  python sistema/gerar_demo.py [pasta_de_saida]
Cria takeout-demo.zip (por padrão em entrada/) com um perfil principal e um perfil Kids chamado "Crianca".
A história simulada: nos primeiros meses a criança assiste no perfil Kids; depois passa a assistir no
perfil principal, com carga crescente e pico no fim da tarde dos dias úteis.
"""
import html, os, random, sys, zipfile
from datetime import datetime, timedelta

random.seed(42)
MESES = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez']

INFANTIL = {
    'SBT Kids': ['O Picapau Amarelo - Episódio {n} Completo', 'Domingo Animado Completo ({n})'],
    'Palavra Cantada Oficial': ['Palavra Cantada | Canção {n}', 'Palavra Cantada | Brincadeira {n}'],
    'Tiquequê': ['Tiquequê | Música {n}'],
    'Cidade Encantada': ['Teatro - Conto de fadas {n} - Cidade Encantada'],
    'Mari Bigio - Cordel Animado': ['Cordel para Crianças {n}'],
    'Maria Clara & JP': ['Maria Clara e JP em aventura {n}'],
    'Mundo Bita': ['Mundo Bita - Clipe {n}'],
    'DisneyMusicVEVO': ['Frozen - Canção {n}', 'Moana - Canção {n}'],
}
PESO_INF = [30, 10, 8, 7, 6, 6, 5, 4]
ADULTO = {
    'Canal de Notícias': ['Jornal da noite {n}', 'Entrevista especial {n}'],
    'Tecnologia Hoje': ['Review do celular {n}', 'Tutorial de programação {n}'],
    'Podcast Ideias': ['Episódio {n} - conversa sobre carreira'],
    'Música Popular VEVO': ['Clipe oficial {n}'],
    'História em 10 minutos': ['A história de {n}'],
}

def vid():
    return ''.join(random.choice('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_') for _ in range(11))

POOL_INF = {c: [(vid(), t.format(n=random.randint(1, 40))) for t in ts for _ in range(12)] for c, ts in INFANTIL.items()}
POOL_AD = {c: [(vid(), t.format(n=random.randint(1, 300))) for t in ts for _ in range(40)] for c, ts in ADULTO.items()}

def sessao(inicio, minutos, pool, pesos, eventos, gap=(4, 25)):
    t, fim = inicio, inicio + timedelta(minutes=minutos)
    while t < fim:
        canal = random.choices(list(pool), weights=pesos)[0]
        v, titulo = random.choice(pool[canal])
        eventos.append((t, v, titulo, canal))
        t += timedelta(minutes=random.uniform(*gap))

def gerar(fim=None, dias=180):
    fim = fim or datetime.now().replace(hour=21, minute=0, second=0, microsecond=0)
    ini = fim - timedelta(days=dias)
    kids, principal = [], []
    d = ini
    while d < fim:
        prog = (d - ini).days / dias            # 0 → 1 ao longo do período
        util = d.weekday() < 5
        no_kids = prog < 0.45                   # migração para o perfil principal
        destino = kids if no_kids else principal
        carga = 25 + 90 * prog                  # minutos/dia crescentes
        if random.random() < (0.72 if util else 0.85):
            if util:
                if random.random() < 0.25:
                    sessao(d.replace(hour=6, minute=random.randint(30, 59)), random.uniform(10, 25), POOL_INF, PESO_INF, destino)
                sessao(d.replace(hour=random.choice([17, 18, 18, 19]), minute=random.randint(0, 59)), random.uniform(.6, 1.4) * carga, POOL_INF, PESO_INF, destino)
            else:
                for h in random.sample([8, 9, 10, 14, 15, 17, 18], 2):
                    sessao(d.replace(hour=h, minute=random.randint(0, 59)), random.uniform(.5, 1.0) * carga, POOL_INF, PESO_INF, destino)
        for h in random.sample([7, 12, 13, 21, 22, 23], random.randint(1, 3)):  # uso adulto
            sessao(d.replace(hour=h, minute=random.randint(0, 59)), random.uniform(15, 60), POOL_AD, None, principal, gap=(3, 15))
        d += timedelta(days=1)
    return kids, principal

def celula(t, v, titulo, canal, com_titulo):
    data = f'{t.day} de {MESES[t.month - 1]}. de {t.year}, {t:%H:%M:%S} BRT'
    url = f'https://www.youtube.com/watch?v={v}'
    corpo = (f'Watched\xa0<a href="{url}">{html.escape(titulo)}</a><br><a href="https://www.youtube.com/channel/UC{v}">{html.escape(canal)}</a>'
             if com_titulo else f'Watched\xa0<a href="{url}">{url}</a>')
    return ('<div class="outer-cell mdl-cell mdl-cell--12-col mdl-shadow--2dp"><div class="mdl-grid">'
            f'<div class="content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1">{corpo}<br>{data}<br></div></div></div>')

def documento(eventos, com_titulo):
    eventos = sorted(eventos, reverse=True)
    return '<html><body><div class="mdl-grid">' + ''.join(celula(*e, com_titulo) for e in eventos) + '</div></body></html>'

if __name__ == '__main__':
    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    saida = sys.argv[1] if len(sys.argv) > 1 else os.path.join(raiz, 'entrada')
    os.makedirs(saida, exist_ok=True)
    kids, principal = gerar()
    base = 'Takeout/YouTube e YouTube Music/'
    caminho = os.path.join(saida, 'takeout-demo.zip')
    with zipfile.ZipFile(caminho, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr(base + 'histórico/histórico-de-visualização.html', documento(principal, True))
        # no Takeout real o perfil Kids vem sem título nem canal; aqui vêm preenchidos para a demo funcionar offline
        z.writestr(base + 'crianças/Crianca/histórico-de-visualização.html', documento(kids, True))
    print(f'Takeout fictício criado: {caminho} ({len(principal)} vídeos no perfil principal, {len(kids)} no perfil Kids)')
